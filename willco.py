import os
import time
import cot_reports as cot
import pandas as pd
import numpy as np
import datetime
import requests

class WillCo:

    # Only the columns actually used by calculateWillCo()
    REQUIRED_CSV_COLUMNS = [
        'cftc_contract_market_code',
        'market_and_exchange_names',
        'as_of_date_in_form_yyyy_mm_dd',
        'q_commercials',
        'q_large_speculators',
        'q_small_speculators',
        'commercials_net_percent',
        'large_speculators_net_percent',
        'small_speculators_net_percent',
        'percent_commercials_long',
        'percent_large_speculators_long',
        'percent_small_speculators_long',
    ]

    # Specify dtypes for faster CSV reading (bottleneck #10.2 fix)
    CSV_DTYPES = {
        'cftc_contract_market_code': str,
        'market_and_exchange_names': str,
        'as_of_date_in_form_yyyy_mm_dd': str,
        'q_commercials': float,
        'q_large_speculators': float,
        'q_small_speculators': float,
        'commercials_net_percent': float,
        'large_speculators_net_percent': float,
        'small_speculators_net_percent': float,
        'percent_commercials_long': float,
        'percent_large_speculators_long': float,
        'percent_small_speculators_long': float,
    }

    def __init__(self, csv_path):
        self.csv_path = csv_path
        if not os.path.exists(self.csv_path):
            self.fetch_and_store_cot_data()

    def read_csv(self):
        return pd.read_csv(
            self.csv_path,
            usecols=self.REQUIRED_CSV_COLUMNS,
            dtype=self.CSV_DTYPES
        )

    def _cot_year_with_retry(self, year, cot_report_type='legacy_fut', attempts=5):
        """Download one COT year with retries for transient network failures."""
        delay = 2.0
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                return pd.DataFrame(cot.cot_year(year, cot_report_type=cot_report_type))
            except (requests.exceptions.RequestException, ConnectionError, OSError, TimeoutError) as exc:
                last_error = exc
                if attempt == attempts:
                    break
                print(
                    f"COT download for {year} failed (attempt {attempt}/{attempts}): {exc!r}; "
                    f"retrying in {delay:.0f}s..."
                )
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
        raise RuntimeError(f"Failed to download COT data for {year} after {attempts} attempts") from last_error

    def fetch_and_store_cot_data(self):
        end_year = int(datetime.date.today().strftime('%Y')) + 1
        begin_year = end_year - 7
        yearly_frames = []
        for i in reversed(range(begin_year, end_year)):
            single_year = self._cot_year_with_retry(i, cot_report_type='legacy_fut')
            yearly_frames.append(single_year)
            # Brief pause between years to reduce CFTC connection resets under load.
            time.sleep(1.0)
        df = pd.concat(yearly_frames, ignore_index=True) if yearly_frames else pd.DataFrame()

        df = df.rename(columns=lambda x: x.replace(' ', '_').replace('-', '_').replace('(', '_').replace(')', '_').lower())
        df.replace('.', 0, inplace=True)

        df['market_and_exchange_names'] = df['market_and_exchange_names'].str.split(' - ', expand=True)[0]

        df['commercials_net_value'] = df["commercial_positions_long__all_"] - df["commercial_positions_short__all_"]
        df['large_speculators_net_value'] = df["noncommercial_positions_long__all_"] - df["noncommercial_positions_short__all_"]
        df['small_speculators_net_value'] = df["nonreportable_positions_long__all_"] - df["nonreportable_positions_short__all_"]

        df['commercials_net_change'] = df['change_in_commercial_long__all_'].astype(int) - df['change_in_commercial_short__all_'].astype(int)
        df['large_speculators_net_change'] = df['change_in_noncommercial_long__all_'].astype(int) - df['change_in_noncommercial_short__all_'].astype(int)
        df['small_speculators_net_change'] = df['change_in_nonreportable_long__all_'].astype(int) - df['change_in_nonreportable_short__all_'].astype(int)

        df['q_commercials'] = df['commercials_net_value'] / df['open_interest__all_']
        df['q_large_speculators'] = df['large_speculators_net_value'] / df['open_interest__all_']
        df['q_small_speculators'] = df['small_speculators_net_value'] / df['open_interest__all_']

        df['commercial_full_long_plus_short_position'] = df['commercial_positions_long__all_'] + df["commercial_positions_short__all_"]
        df['large_speculators_full_long_plus_short_position'] = df['noncommercial_positions_long__all_'] + df["noncommercial_positions_short__all_"]
        df['small_speculators_full_long_plus_short_position'] = df['nonreportable_positions_long__all_'] + df["nonreportable_positions_short__all_"]

        df['percent_commercials_long'] = df['commercial_positions_long__all_'] / df['commercial_full_long_plus_short_position']
        df['percent_commercials_short'] = df['commercial_positions_short__all_'] / df['commercial_full_long_plus_short_position']
        df['percent_large_speculators_long'] = df['noncommercial_positions_long__all_'] / df['large_speculators_full_long_plus_short_position']
        df['percent_large_speculators_short'] = df['noncommercial_positions_short__all_'] / df['large_speculators_full_long_plus_short_position']
        df['percent_small_speculators_long'] = df['nonreportable_positions_long__all_'] / df['small_speculators_full_long_plus_short_position']
        df['percent_small_speculators_short'] = df['nonreportable_positions_short__all_'] / df['small_speculators_full_long_plus_short_position']

        df['commercials_net_percent'] = df['percent_commercials_long'] - df['percent_commercials_short']
        df['large_speculators_net_percent'] = df['percent_large_speculators_long'] - df['percent_large_speculators_short']
        df['small_speculators_net_percent'] = df['percent_small_speculators_long'] - df['percent_small_speculators_short']

        

        df.to_csv(self.csv_path, index=False)

    def calculateWillCo(self, df, market, weeks):
        asset = df[df['cftc_contract_market_code'] == market].copy()

        asset['lookback_(y)'] = "{:.1f}".format(weeks / 52)

        available = len(asset)
        n = min(weeks + 1, available)
        pad = weeks + 1 - n

        # Optimized: Use NumPy arrays instead of Python lists for better performance
        qCommercials = np.pad(asset['q_commercials'].iloc[:n].values, (0, pad), constant_values=0)
        qLargeSpeculators = np.pad(asset['q_large_speculators'].iloc[:n].values, (0, pad), constant_values=0)
        qSmallSpeculators = np.pad(asset['q_small_speculators'].iloc[:n].values, (0, pad), constant_values=0)

        minQCommercialsNWeeks = qCommercials.min()
        maxQCommercialsNWeeks = qCommercials.max()

        minQLargeSpeculatorsNWeeks = qLargeSpeculators.min()
        maxQLargeSpeculatorsNWeeks = qLargeSpeculators.max()

        minQSmallpeculatorsNWeeks = qSmallSpeculators.min()
        maxQSmallpeculatorsNWeeks = qSmallSpeculators.max()

        commercials_range = maxQCommercialsNWeeks - minQCommercialsNWeeks
        large_specs_range = maxQLargeSpeculatorsNWeeks - minQLargeSpeculatorsNWeeks
        small_specs_range = maxQSmallpeculatorsNWeeks - minQSmallpeculatorsNWeeks

        asset['willco_commercials_index'] = round(((asset.iloc[0]['q_commercials'] - minQCommercialsNWeeks) / commercials_range) * 100) if commercials_range != 0 else 50
        asset['willco_large_specs_index'] = round(((asset.iloc[0]['q_large_speculators'] - minQLargeSpeculatorsNWeeks) / large_specs_range) * 100) if large_specs_range != 0 else 50
        asset['willco_small_specs_index'] = round(((asset.iloc[0]['q_small_speculators'] - minQSmallpeculatorsNWeeks) / small_specs_range) * 100) if small_specs_range != 0 else 50

        if weeks == 26:
            asset['commercials_net_(%)'] = (asset.iloc[0]['commercials_net_percent'].round(2) * 100).astype(int)
            asset['large_speculators_net_(%)'] = (asset.iloc[0]['large_speculators_net_percent'].round(2) * 100).astype(int)
            asset['small_speculators_net_(%)'] = (asset.iloc[0]['small_speculators_net_percent'].round(2) * 100).astype(int)

            asset['commercials_change_(%)'] = ((asset.iloc[0]['percent_commercials_long'] - asset.iloc[1]['percent_commercials_long']).round(2) * 100).astype(int)
            asset['large_speculators_change_(%)'] = ((asset.iloc[0]['percent_large_speculators_long'] - asset.iloc[1]['percent_large_speculators_long']).round(2) * 100).astype(int)
            asset['small_speculators_change_(%)'] = ((asset.iloc[0]['percent_small_speculators_long'] - asset.iloc[1]['percent_small_speculators_long']).round(2) * 100).astype(int)
        else:
            asset['commercials_net_(%)'] = 0
            asset['large_speculators_net_(%)'] = 0
            asset['small_speculators_net_(%)'] = 0

            asset['commercials_change_(%)'] = 0
            asset['large_speculators_change_(%)'] = 0
            asset['small_speculators_change_(%)'] = 0


        return asset.head(1)[['market_and_exchange_names', 'lookback_(y)', 
                              'willco_commercials_index', 'willco_large_specs_index', 'willco_small_specs_index', 
                              'commercials_change_(%)', 'large_speculators_change_(%)', 'small_speculators_change_(%)',
                              'commercials_net_(%)', 'large_speculators_net_(%)', 'small_speculators_net_(%)', 'as_of_date_in_form_yyyy_mm_dd']]
    
