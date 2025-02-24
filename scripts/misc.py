"""
Other miscellaneous functions for GMAC analysis.
"""

import sys
import pathlib
import numpy as np
import pandas as pd
from scipy import signal
from sklearn.neighbors import KernelDensity
from scipy import stats
from sklearn.metrics import confusion_matrix, make_scorer


def computer_tilt_for_all_subjects(alldf: pd.DataFrame, accl_lbl: str, nwin: int, causal: bool=True) -> dict:
    """
    Computes the tilt angle for all subjects in the given dataframe.
    """
    return {
        _subj: np.hstack([
            compute_tilt(alldf.loc[(alldf.loc[:, 'subject'] == _subj) &
                                   (alldf.loc[:, 'segment'] == seg), accl_lbl],
                         nwin, causal)
            for seg in alldf.loc[alldf.loc[:, 'subject'] == _subj, 'segment'].unique()])
        for _subj in np.unique(alldf.subject)
    }


def compute_tilt(accl_farm: np.array, nwin: int, causal: bool=True) -> np.array:
    """
    Computes the tilt angle from the accelerometer data.
    """
    if causal:
        # Moving averaging using the causal filter
        acclf = signal.lfilter(np.ones(nwin) / nwin, 1, accl_farm)
    else:
        # Moving averaging using the Savitzky-Golay filter
        acclf = signal.savgol_filter(accl_farm, window_length=nwin, polyorder=0,
                                    mode='constant')
    acclf[acclf < -1] = -1
    acclf[acclf > 1] = 1
    return -np.rad2deg(np.arccos(acclf)) + 90


def read_data(subject_type, base_path="../data/") -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Reads raw data from 'subject_type' folder
    """
    if subject_type == 'patient':
        left = pd.read_csv(pathlib.Path(base_path, subject_type, "affected.csv"),
                           parse_dates=['time'], index_col='time')
        right = pd.read_csv(pathlib.Path(base_path, subject_type, "unaffected.csv"),
                            parse_dates=['time'], index_col='time')
    elif subject_type == 'control':
        left = pd.read_csv(pathlib.Path(base_path, subject_type, "left.csv"),
                           parse_dates=['time'], index_col='time')
        right = pd.read_csv(pathlib.Path(base_path, subject_type, "right.csv"),
                            parse_dates=['time'], index_col='time')
    else:
        raise ValueError(f"Invalid parameter: {subject_type}. Use 'control' or 'patient' instead.")
    return left, right


def assign_segments(df: pd.DataFrame, dur_th:float = 1, dT: float = 0.02) -> pd.DataFrame:
    """
    Assign a semgment column to the given dataframe, while removing segments
    shorter than 'dur_th' seconds.
    """
    # Get semgent indices
    _dtimes = np.array([pd.Timedelta(_dt).total_seconds()
                        for _dt in np.diff(df.index.values)])
    inx = np.hstack((0, np.where(_dtimes > dT)[0] + 1, len(df)))
    seginx = list(zip(inx[0:-1], inx[1:]))

    # Filter segments based on segment duration. Anything shorter than 5s is out.
    seginx = [ix for ix in seginx
            if pd.Timedelta(df.index[ix[1]-1] - df.index[ix[0]]).total_seconds() > dur_th]

    # Create new dataframe without short segments
    df = df.iloc[np.hstack([np.arange(ix[0], ix[1]) for ix in seginx])]

    # Assign segment column
    _segdf = pd.DataFrame(
        {"segment": np.hstack([[i] * (ix[1] - ix[0]) for i, ix in enumerate(seginx)])},
        index=df.index
    )
    
    return pd.concat([df, _segdf], axis=1)


def get_continuous_segments(df: pd.DataFrame, tjump: float=1) -> list[pd.DataFrame]:
    # returns a list of continuous sections (as dataframes) from the original dataframe

    time_diff = np.array([pd.Timedelta(diff).total_seconds()
                          for diff in np.diff(df.index.values)])
    inx = np.sort(np.append(np.where(time_diff > tjump)[0], -1))
    dfs = [df.iloc[inx[i] + 1:inx[i + 1] + 1]
           if i + 1 < len(inx)
           else df.iloc[inx[-1] + 1:]
           for i in np.arange(len(inx))]
    return dfs


def compute_vector_magnitude(imudf: pd.DataFrame) -> pd.DataFrame:
    """Computes the vector magnitude from the IMU data.
    """
    imudf = imudf.loc[:, ['ax', 'ay', 'az', 'gx', 'gy', 'gz']]
    imudf = resample(imudf, 30)

    gyr = np.array(imudf[['gx', 'gy', 'gz']])
    acc = np.array(imudf[['ax', 'ay', 'az']])

    g = np.array([0, 0, 1])
    ae = np.empty([len(acc), 3])

    mg = Madgwick(frequency=30, beta=0.5)
    q = np.tile([1., 0., 0., 0.], (len(acc), 1))

    r = orientation.q2R(mg.updateIMU(q[0], gyr[0], acc[0]))
    ae[0] = np.matmul(r, acc[0]) - g

    for i in range(1, len(acc)):
        q[i] = mg.updateIMU(q[i - 1], gyr[i], acc[i])
        r = orientation.q2R(q[i])
        ae[i] = np.matmul(r, acc[i]) - g

    out_df = pd.DataFrame(index=imudf.index)
    out_df['ax'] = bandpass(np.nan_to_num(ae[:, 0]), fs=30)
    out_df['ay'] = bandpass(np.nan_to_num(ae[:, 1]), fs=30)
    out_df['az'] = bandpass(np.nan_to_num(ae[:, 2]), fs=30)
    out_df = resample(out_df, 10)

    out_df['ax'] = np.where(np.absolute(out_df['ax'].values) < 0.068, 0, out_df['ax'].values) / 0.01664
    out_df['ay'] = np.where(np.absolute(out_df['ay'].values) < 0.068, 0, out_df['ay'].values) / 0.01664
    out_df['az'] = np.where(np.absolute(out_df['az'].values) < 0.068, 0, out_df['az'].values) / 0.01664

    dfs = get_continuous_segments(out_df, tjump=1)
    dfs = [df.resample(str(1) + 'S').sum() for df in dfs]
    out_df = pd.concat(dfs)
    out_df.index.name = 'time'
    out_df = out_df.fillna(0)

    out_df['a_mag'] = [np.linalg.norm(x) for x in np.array(out_df[['ax', 'ay', 'az']])]
    out_df['counts'] = [np.round(x) for x in out_df['a_mag'].rolling(5).mean()]
    return out_df[['counts']]


def resample(df: pd.DataFrame, new_fs: float) -> pd.DataFrame:
    """
    Resample different continuous segments of the given dataframe at the new 
    sampling frequency.
    """
    dfs = get_continuous_segments(df)
    dfs = [df.resample(f'{str(round(1 / new_fs, 2))}S', label='right', closed='right').mean()
           for df in dfs]
    df = pd.concat(dfs)
    df.index.name = 'time'
    return df


def bandpass(x: np.array, fs: float=50, order: int=4) ->  np.array:
    """
    Bandpass filter the signal between 0.25 and 2.5 Hz for computing the vector 
    magnitude using the IMU data.
    """
    sos = signal.butter(order, [0.25, 2.5], 'bandpass', fs=fs, output='sos', analog=False)
    filtered = signal.sosfilt(sos, x)
    return filtered


def compute_accl_magnitude(accl: np.array, time: np.array, nfilt: int=5,
                           causal: bool=True) -> pd.DataFrame:
    """
    Compute the magnitude of the accelerometer signal.
    """
    if causal:
        sos = signal.butter(2, 1/(2*50), 'high', output='sos')
        accl_filt = np.array([signal.sosfilt(sos, accl[:, 0]),
                              signal.sosfilt(sos, accl[:, 1]),
                              signal.sosfilt(sos, accl[:, 2])]).T
    else:
        b, a = signal.butter(2, 1/(2*50), 'high')
        accl_filt = signal.filtfilt(b, a, accl, axis=0)
    
    # Zero low acceleration
    deadband_threshold = 0.068
    accl_filt[np.abs(accl_filt)<deadband_threshold] = 0
    
    amag_1 = np.linalg.norm(accl_filt, axis=1)
    times = time.astype('datetime64[s]')
    amag_1 = np.array([np.sum(amag_1[np.where(times == _ts)[0]])
                       for _ts in np.unique(times)])
    if causal:
        # moving average filter
        _input = np.append(np.ones(nfilt - 1) * amag_1[0], amag_1)
        amag_df = pd.DataFrame(
            np.convolve(_input, np.ones(nfilt), mode='valid') / nfilt,
            columns=['mag'],
            index=np.sort(np.unique(times))
        )
    else:
        amag_df = pd.DataFrame(
            signal.savgol_filter(amag_1, window_length=nfilt, polyorder=0,
                                 mode='constant'),
            columns=['mag'],
            index=np.sort(np.unique(times))
        )
    amag_df.index.name = 'time'        
    return amag_df


def read_summarize_data(datadir: str, dT: float) -> dict:
    """Read organize data from all control subjects and patients.
    """
    # Read healthy and control data
    left, right = read_data(subject_type='control', base_path=datadir)
    aff, unaff = read_data(subject_type='patient', base_path=datadir)

    # Assign segments for each subject
    left = pd.concat([assign_segments(left[left.subject == subj],
                                           dur_th=1, dT=dT)
                      for subj in left.subject.unique()], axis=0)
    right = pd.concat([assign_segments(right[right.subject == subj],
                                            dur_th=1, dT=dT)
                       for subj in right.subject.unique()])
    aff = pd.concat([assign_segments(aff[aff.subject == subj],
                                          dur_th=1, dT=dT)
                     for subj in aff.subject.unique()])
    unaff = pd.concat([assign_segments(unaff[unaff.subject == subj],
                                            dur_th=1, dT=dT)
                       for subj in unaff.subject.unique()])

    # All limbs data ddf
    return {
        "left": left,
        "right": right,
        "aff": aff,
        "unaff": unaff
    }


def autcorr(x):
    """Compute the autocorrelation function for the given signal.
    """
    if np.std(x) == 0:
        return np.zeros(len(x))
    x_hat = (x - np.mean(x))
    _ac = np.correlate(x_hat, x_hat, mode='full')[len(x)-1:]
    return _ac / _ac[0]


def get_autocorr(dframe, subj, seg, cols=['r1', 'r2', 'g1', 'g2']):
    """Compute the autcorrelation function for the given subject and segment.
    """
    _subinx = (dframe['subject'] == subj)
    _seginx = (dframe['segment'] == seg)
    _uluse = dframe[_subinx & _seginx][cols].values
    return np.array([autcorr(_u) for _u in _uluse.T])    


def iterate_dataframes(dframe: dict) -> tuple:
    """Get all the indices for the given subject and segment.
    """
    for key, _df in dframe.items():
        for subj in _df['subject'].unique():
            for seg in _df[_df['subject'] == subj]['segment'].unique():
                yield (key, subj, seg)


def genrate_ul_autocorr_summary(datadf, N=4000):
    # Compute the autocorrelation for all subjects, and segments.
    uluse_acorr = {}
    for limb, subj, seg in iterate_dataframes(datadf):
        sys.stdout.write(f"\r{limb:>5s}, {subj:2d}, {seg:2d}")
        if limb not in uluse_acorr:
            uluse_acorr[limb] = {}
        if subj not in uluse_acorr[limb]:
            uluse_acorr[limb][subj] = {}
        uluse_acorr[limb][subj][seg] = get_autocorr(datadf[limb], subj, seg)

    # Generate summary of autocorrelation functions for healthy
    all_ul = {}
    for limb in ['left', 'right']:
        all_ul[limb] = None
        for subj, _ac in uluse_acorr[limb].items():
            for seg, _acorr in _ac.items():
                if all_ul[limb] is None:
                    all_ul[limb] = _acorr.T[:N, :]
                elif _acorr.T.shape[0] >= N:
                    all_ul[limb] = np.hstack((all_ul[limb],
                                                _acorr.T[:N, :]))
    # Generate summary of autocorrelation functions for patients
    for limb in ['aff', 'unaff']:
        all_ul[limb] = None
        for subj, _ac in uluse_acorr[limb].items():
            for seg, _acorr in _ac.items():
                if all_ul[limb] is None:
                    all_ul[limb] = _acorr.T[:N, :]
                elif _acorr.T.shape[0] >= N:
                    all_ul[limb] = np.hstack((all_ul[limb],
                                                  _acorr.T[:N, :]))
    return all_ul


def entropy(data):
    data = np.array(data).reshape(-1, 1)
    # estimate pdf using KDE with gaussian kernel
    kde = KernelDensity(kernel='gaussian', bandwidth=0.2).fit(data)
    log_p = kde.score_samples(data)
    return stats.entropy(np.exp(log_p))


# Compute the features for given data frame.
def get_uluse_from_raters(ratings: np.array) -> np.array:
    _mean = np.mean(ratings, axis=1)
    _uluse = _mean > 0.5
    _uluse[_mean == 0.5] = np.random.rand(np.sum(_mean == 0.5)) > 0.5
    return _uluse


def compute_all_features(datadf: pd.DataFrame, Nin: int) -> pd.DataFrame:
    """Computes all the features using the given data frame.
    """
    # UL use
    _uludf = pd.DataFrame(1.0 * get_uluse_from_raters(datadf[["r1", "r2", "g1", "g2"]].values),
                          columns=["uluse"])
    # Accl. norm
    anormdf = pd.DataFrame(np.linalg.norm(datadf[["ax", "ay", "az"]].values, axis=1),
                           columns=["norm"])

    # Form the data frame
    return pd.DataFrame.from_dict({
        "time": datadf.index,
        "uluse": _uludf["uluse"].values,
        "mean_x": datadf["ax"].rolling(window=Nin, min_periods=1).mean().values,
        "mean_y": datadf["ay"].rolling(window=Nin, min_periods=1).mean().values,
        "mean_z": datadf["az"].rolling(window=Nin, min_periods=1).mean().values,
        "var_x": datadf["ax"].rolling(window=Nin, min_periods=1).var().fillna(0).values,
        "var_y": datadf["ay"].rolling(window=Nin, min_periods=1).var().fillna(0).values,
        "var_z": datadf["az"].rolling(window=Nin, min_periods=1).var().fillna(0).values,
        "mean_2": anormdf["norm"].rolling(window=Nin, min_periods=1).mean().values,
        "var_2": anormdf["norm"].rolling(window=Nin, min_periods=1).var().fillna(0).values,
        "min_2": anormdf["norm"].rolling(window=Nin, min_periods=1).min().values,
        "max_2": anormdf["norm"].rolling(window=Nin, min_periods=1).max().values,
        "ent_2": anormdf["norm"].rolling(window=Nin, min_periods=1).apply(entropy).values
    }).set_index("time")


def get_data_segment(datadf, limb, subj, seg) -> pd.DataFrame:
    """Returns the dataframe corresponding to the limb, subject, and segment.
    """
    _sbjinx = (datadf[limb]["subject"] == subj)
    _seginx = (datadf[limb]["segment"] == seg)
    return datadf[limb][_sbjinx & _seginx]


def get_features_for_all(datadf: pd.DataFrame, Nin: int, raw_cols: list[str]) -> pd.DataFrame:
    """Computes the features for all the segments for both limbs, of all subjects.
    """
    # Compute the features for each segment for each patient.
    data_features_df = {}
    _allinx = [_inx for _inx in iterate_dataframes(datadf)]
    for i, (limb, subj, seg) in enumerate(_allinx):
        sys.stdout.write(f"\r[{i:3d} / {len(_allinx):3d}] {limb:>5s} {subj:2d} {seg:2d}")
        # Get the dataframe for the segment
        _df = get_data_segment(datadf, limb, subj, seg)
        # Compute the features
        _features = compute_all_features(_df, Nin)
        
        # Check if the limb exists.
        if limb not in data_features_df:
            data_features_df[limb] = pd.DataFrame(columns=raw_cols + list(_features.columns))
        data_features_df[limb] = pd.concat(
            (data_features_df[limb],
            _df[raw_cols].join(_features, how="left").reset_index(drop=False)),
            axis=0,
            ignore_index=False
        )
    return data_features_df 

def generate_windowed_uluse(datadf):
    """Generates a windowed version of the uluse data.
    """
    _ulusew = datadf['uluse'].rolling(window=Nin, min_periods=1).mean().values
    _ulusew[_ulusew > 0.5] = 1.0
    _ulusew[_ulusew < 0.5] = 0.0
    _ulusew[_ulusew == 0.5] = np.random.choice([0, 1], size=np.sum(_ulusew == 0.5))
    return _ulusew

def outerloop_loso_split(limbdf, subcol):
    """Iterates over the leave-one-subject-out training and testing data.
    """
    for subj, subjdf in limbdf.groupby(subcol):
        yield (limbdf[limbdf[subcol] != subj], subjdf)

def innerloop_loso_split(train_df, subcol):
    """Iterates over the leave-one-segment-out training and validation data.
    """
    _tempdf = train_df.copy()
    _tempdf['inx'] = range(_tempdf.shape[0])
    folds = []
    for subj, subjdf in _tempdf.groupby(subcol):
        val_inx = subjdf['inx'].values
        train_inx = _tempdf[_tempdf[subcol] != subj]['inx'].values
        folds.append((train_inx, val_inx))
    return folds

# Define Youden index function
def youden_index_score(y_true, y_pred):
    # Compute confusion matrix
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred).ravel()
    
    # Sensitivity (Recall for Positive Class)
    sensitivity = tp / (tp + fn) if (tp + fn) > 0 else 0
    
    # Specificity
    specificity = tn / (tn + fp) if (tn + fp) > 0 else 0
    
    # Youden index
    youden_index = sensitivity + specificity - 1
    return youden_index

# Wrap it into a scorer
youden_scorer = make_scorer(youden_index_score, greater_is_better=True)

# def get_largest_continuous_segment_indices(data: pd.DataFrame, subject: int,
#                                            deltaT: np.timedelta64) -> tuple[int, int]:
#     """
#     Returns the indices of the longest continuous segment of data for the
#     given subject.
#     """
#     _dtimes = np.diff(data[data.subject == subject].index)
#     _jumpinx = np.hstack(([0], np.where(_dtimes > deltaT)[0]))
#     _inx1 = np.argmax(np.diff(_jumpinx))
#     return _jumpinx[_inx1], _jumpinx[_inx1 + 1] + 1


# def get_segmented_data(data: pd.DataFrame, deltaT: np.timedelta64) -> dict[int, pd.DataFrame]:
#     """
#     Returns the segmented data for each subject in the given data.
#     """
#     # Go through each subject and get the longest continuous segments of data
#     subjs = np.unique(data.subject)
#     # Segment left data
#     _subjdata = {}
#     for _subj in subjs:
#         # Get indices of the longest continuous segment of data
#         inx = get_largest_continuous_segment_indices(data, _subj, deltaT)
#         _subjdata[_subj] = data[data.subject == _subj].iloc[inx[0]:inx[1]]
#     return _subjdata
