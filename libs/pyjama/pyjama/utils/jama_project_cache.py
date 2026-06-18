"""Project directory caching for Jama Connect API."""
from datetime import datetime
from typing import Any, Dict, Optional
import logging

from .jama_constants import (
    CACHE_PROJECTS_SUBDIR,
    PROJECT_DIR_PREFIX,
    FIELDS_KEY,
    NAME_KEY,
    ID_KEY,
)
from .cache_manager import CacheMode, DiskCacheManager

# Logical slot used with DiskCacheManager's session-refresh tracking.
PROJECT_CACHE_KEY = "projects"


class JamaProjectCache:
    """
    Manages cached project directory for fast project name -> ID lookups.

    Caches project metadata to disk to avoid repeated API calls for project
    resolution. Automatically refreshes from API when projects are not found.

    Jama-specific resolution logic only — all disk IO, timestamping, and cache
    mode decisions are delegated to a shared :class:`DiskCacheManager` so the
    project directory lives under the same cache root (``<root>/projects/``) as
    the rest of the tier-3 cache.
    """

    def __init__(
        self,
        jama_client: Any,
        cache_manager: DiskCacheManager,
        cache_folder: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize project cache.

        Args:
            jama_client: Authenticated JamaClient instance
            cache_manager: Shared DiskCacheManager providing the disk tier
                (cache root, mode logic, read/write helpers)
            cache_folder: Override the default folder
                (``cache_manager.resolve_folder("projects")``)
            logger: Optional logger instance
        """
        self.client = jama_client
        self._cache = cache_manager
        self.cache_mode = cache_manager.mode
        self.cache_folder = cache_folder or cache_manager.resolve_folder(CACHE_PROJECTS_SUBDIR)
        self.logger = logger or logging.getLogger(__name__)

        self._directory: Optional[Dict[str, Any]] = None
        self._directory_path: Optional[str] = None

        # Load existing cache only in USE mode; OFF never reads disk and
        # REFRESH must ignore existing files on the first resolve.
        if self.cache_mode is CacheMode.USE:
            self.load()

    def get_latest_cache_file(self) -> Optional[str]:
        """Find the most recent cache file based on modification time."""
        latest_file = self._cache.newest_file(self.cache_folder, PROJECT_DIR_PREFIX, ".json")

        if not latest_file:
            self.logger.info("No existing project cache files found")
            return None

        self.logger.info("Found latest project cache: %s", latest_file)
        return latest_file

    def load(self) -> bool:
        """Load project directory from the latest cached file."""
        self.logger.info("Loading project directory from cache")

        latest_file = self.get_latest_cache_file()

        if not latest_file:
            self.logger.info("No cached project directory found")
            return False

        try:
            self._directory = self._cache.read_json(latest_file)
            self._directory_path = latest_file

            timestamp = self._directory.get("timestamp", "unknown")
            project_count = len(self._directory.get("projects", {}))

            self.logger.info("Loaded project directory (timestamp: %s, projects: %d)",
                           timestamp, project_count)
            return True

        except Exception as e:
            self.logger.error("Failed to load cache from %s: %s", latest_file, str(e))
            self._directory = None
            self._directory_path = None
            return False
    
    def refresh(self) -> None:
        """Fetch fresh project data from Jama API and save to cache."""
        self.logger.info("Refreshing project directory from Jama API")
        
        try:
            projects_list = self.client.get_projects()
            self.logger.info("Retrieved %d projects from Jama", len(projects_list))
            
            # Build directory indexed by project name
            projects_dict = {}
            for project in projects_list:
                fields = project.get(FIELDS_KEY, {})
                project_name = fields.get(NAME_KEY, "")
                
                if project_name:
                    projects_dict[project_name] = project
                    self.logger.debug("Indexed project: %s (ID: %d)",
                                    project_name, project.get(ID_KEY))
            
            self._directory = {
                "timestamp": datetime.now().isoformat(),
                "projects": projects_dict
            }
            
            self.save()
            
            self.logger.info("Successfully refreshed project directory with %d projects",
                           len(projects_dict))
            
        except Exception as e:
            self.logger.error("Failed to refresh project directory: %s", str(e))
            raise
    
    def save(self) -> None:
        """Save the current project directory to disk with timestamp."""
        if not self._cache.writes_enabled():
            self.logger.debug("Cache mode OFF: skipping project directory save")
            return

        if not self._directory:
            self.logger.warning("No project directory to save")
            return

        try:
            filepath = self._cache.write_json(
                self.cache_folder,
                PROJECT_DIR_PREFIX,
                self._cache.timestamp(),
                self._directory,
            )
            self._directory_path = filepath
            self.logger.info("Saved project directory to: %s", filepath)

        except Exception as e:
            self.logger.error("Failed to save project directory: %s", str(e))
            raise
    
    def lookup_project_id(
        self,
        project_name: str,
        api_id_key: Optional[str] = None
    ) -> Optional[int]:
        """
        Look up project ID from cached directory.
        
        Args:
            project_name: Name of the project
            api_id_key: Key for project ID in directory
            
        Returns:
            Project ID if found, None otherwise
        """
        api_id_key = api_id_key or ID_KEY
        
        if not self._directory:
            self.logger.debug("No project directory loaded")
            return None
        
        projects = self._directory.get("projects", {})
        project_data = projects.get(project_name)
        
        if not project_data:
            self.logger.debug("Project '%s' not found in cache", project_name)
            return None
        
        project_id = project_data.get(api_id_key)
        
        if project_id:
            self.logger.debug("Found project '%s' with ID: %d", project_name, project_id)
        
        return project_id
    
    def resolve_project_id(
        self,
        project_name: str,
        api_id_key: Optional[str] = None
    ) -> int:
        """
        Resolve project name to ID, refreshing cache if needed.
        
        Process:
        1. Try lookup in cached directory
        2. If not found, refresh from API
        3. Try lookup again
        4. If still not found, raise helpful error
        
        Args:
            project_name: Name of the project
            api_id_key: Key for project ID in API responses
            
        Returns:
            Project ID
            
        Raises:
            ValueError: If project cannot be found after refresh
        """
        api_id_key = api_id_key or ID_KEY

        self.logger.info("Resolving project ID for: '%s'", project_name)

        # Forced recompute: OFF every call; REFRESH on first resolve this session.
        if self._cache.should_recompute(PROJECT_CACHE_KEY):
            self.logger.info("Cache mode %s: refreshing project directory from API",
                           self.cache_mode.value)
            try:
                self.refresh()
                self._cache.mark_refreshed(PROJECT_CACHE_KEY)
            except Exception as e:
                self.logger.error("Failed to refresh cache: %s", str(e))
                raise ValueError(
                    f"Could not refresh project directory from Jama API. "
                    f"Check connection and credentials. Error: {str(e)}"
                )
            project_id = self.lookup_project_id(project_name, api_id_key)
            if project_id:
                self.logger.info("Resolved '%s' to ID %d (after refresh)",
                               project_name, project_id)
                return project_id
            return self._raise_project_not_found(project_name)

        # First attempt: lookup in cache
        project_id = self.lookup_project_id(project_name, api_id_key)

        if project_id:
            self.logger.info("Resolved '%s' to ID %d (from cache)",
                           project_name, project_id)
            return project_id

        # Second attempt: refresh and retry
        self.logger.info("Project not in cache, refreshing from API")

        try:
            self.refresh()
        except Exception as e:
            self.logger.error("Failed to refresh cache: %s", str(e))
            raise ValueError(
                f"Could not refresh project directory from Jama API. "
                f"Check connection and credentials. Error: {str(e)}"
            )

        project_id = self.lookup_project_id(project_name, api_id_key)

        if project_id:
            self.logger.info("Resolved '%s' to ID %d (after refresh)",
                           project_name, project_id)
            return project_id

        return self._raise_project_not_found(project_name)

    def _raise_project_not_found(self, project_name: str) -> int:
        """Raise a helpful ValueError listing available project names."""
        available_projects = list(self._directory.get("projects", {}).keys()) if self._directory else []

        self.logger.error("Project '%s' not found after refresh", project_name)

        error_msg = (
            f"Project '{project_name}' not found in Jama instance. "
            f"Check spelling matches Jama server.\n\n"
            f"Available projects ({len(available_projects)}):\n"
        )

        for proj in available_projects[:10]:
            error_msg += f"  - {proj}\n"

        if len(available_projects) > 10:
            error_msg += f"  ... and {len(available_projects) - 10} more\n"

        raise ValueError(error_msg)
