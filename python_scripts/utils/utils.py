import pandas as pd
import numpy as np

def clean_columns(df):
    df.columns = (
        df.columns.astype(str)
        .str.lower()
        .str.strip()
        .str.replace(" ", "_", regex=False)
        .str.replace("#", "", regex=False)
    )
    return df


def clean_value(x):
    if pd.isna(x):
        return None
    if isinstance(x, pd.Timestamp):
        return x.to_pydatetime()
    return x