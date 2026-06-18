import re
from functools import partial, reduce
from itertools import accumulate, chain
import pandas as pd
from src.utils import pd_utils

class CleanFrame:

    REPLACE_TOKENS=['UI&quot;,', '-f"', '&lt;', '&gt;', '&#39;', 'ui&quot;', '&nbsp;', '&amp;', '&nbsp']
    REPLACE_WITH = ' '

    def __init__(self):
        self.funcs = [
            partial(CleanFrame._clean_doc),
            partial(CleanFrame._replace, replace_tokens=CleanFrame.REPLACE_TOKENS, replace_with=CleanFrame.REPLACE_WITH),
            partial(CleanFrame._make_lower),
            partial(CleanFrame._replace, replace_tokens=CleanFrame.REPLACE_TOKENS, replace_with=CleanFrame.REPLACE_WITH),
            partial(CleanFrame._remove_symbols),
            #partial(CleanFrame._clean_doc),
            partial(CleanFrame._remove_multiple_spaces)
        ]
    
    def run(self, df, apply_to_cols):

        for acol in apply_to_cols:
            df = pd_utils.replace_null(df, acol, ' ')
            df[acol] = df[acol].astype(str)
            df[acol] = df[acol].apply(lambda s: reduce(lambda x, y: y(x), self.funcs,s))
        return df

    @staticmethod
    def _clean_doc(_str):
        _str = re.sub(r'<table (.*?)</table>', ' ', _str, flags=re.DOTALL)
        _str = re.sub(r'<[^>]+>',' ', _str)
        _str = re.sub(r'[id|style|img|src|start|alt|image|height|width|timestamps]+="[^"]+"', ' ', _str)
        _str = re.sub(r'[\t]+', ' ', _str)
        _str = re.sub(r'[\n]+', ' ', _str)
        _str = re.sub(r'[\s]+', ' ', _str)
        return _str

    @staticmethod
    def _replace(_str, replace_tokens, replace_with):
        for tok in replace_tokens:
            _str = _str.replace(tok, replace_with)
        return _str
    
    @staticmethod
    def _make_lower(_str:str) -> str:
        """Make input text lowercase
        Args:
            _str (str): the corpus (str) on which to apply the function
        """
        return _str.lower()

    @staticmethod
    def _remove_symbols(_str:str) -> str:
        """Remove non-word symbols from an input string

        Args:
            _str (str): the corpus (str) on which to apply the function
        """
        # replace symbols and other punctuation with a space
        _str = re.sub(r'(iii\.|ii\.|i\.|\.|“|”|;|-|req#|\/|\{|\}|\(|\)|\,|\"|\'|\:|\+|\*|^\s)', ' ', _str)
        return _str
    
    @staticmethod
    def _remove_multiple_spaces(_str:str) -> str:
        """Replace multiple spaces with a single space

        Args:
            _str (str): the corpus (str) on which to apply the function
        """
        return re.sub(r'[ ]{1,}', ' ', _str)
    
    @staticmethod
    def remove_stopwords(_str:str, STOP_WORDS) -> str:
        """Remove stopwords from a given input string
        
        Args:
            _str (str): the corpus (str) on which to apply the function
        """    
        lst_str = _str.split()
        if STOP_WORDS is not None:
            lst_str = [word for word in lst_str if word not in STOP_WORDS]
        return ' '.join(lst_str)