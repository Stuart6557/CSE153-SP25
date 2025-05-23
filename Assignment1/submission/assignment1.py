# -------------- task 1 --------------

# -*- coding: utf-8 -*-
"""task1.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/18AIfjnTBNUP3mnROFa0iiAtjd6uC9-iH

Midi file player https://midiplayer.ehubsoft.net/

# Setup
"""

!curl -L https://cseweb.ucsd.edu/classes/sp25/cse253-a/data/student_files.tar.gz -o student_files.tar.gz

# !rm student_files.tar.gz

# Make sure to wait until the file fully finishes uploading
# !rm -rf student_files
!tar -xvzf student_files.tar.gz | tail -n 5  # Make sure there's no error message at the tail

!pip install librosa | tail -n 1  # I don't want a super long output
!pip install miditoolkit | tail -n 1  # I don't want a super long output
!pip install xgboost | tail -n 1  # I don't want a super long output
!pip install lightgbm | tail -n 1  # I don't want a super long output
# !pip install autogluon | tail -n 1  # I don't want a super long output

# Probably more imports than are really necessary...
import os
import torch
import torchaudio
from torch.utils.data import Dataset, DataLoader, random_split
import torch.nn as nn
import torch.nn.functional as F
from torchaudio.transforms import MelSpectrogram, AmplitudeToDB
from tqdm import tqdm
import librosa
import numpy as np
import miditoolkit
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, average_precision_score, accuracy_score
import random

from mido import MidiFile
from sklearn.model_selection import train_test_split
from music21 import converter, chord, stream
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBClassifier
from sklearn.ensemble import GradientBoostingClassifier
from lightgbm import LGBMClassifier
from autogluon.tabular import TabularDataset, TabularPredictor
import pandas as pd

"""# Getting data"""

dataroot1 = "student_files/task1_composer_classification/"

def features(path):
    midi_obj = miditoolkit.midi.parser.MidiFile(dataroot1 + '/' + path)

    notes = midi_obj.instruments[0].notes

    # baseline (2 features)
    num_notes = len(notes)
    average_pitch = sum([note.pitch for note in notes]) / num_notes
    average_duration = sum([note.end - note.start for note in notes]) / num_notes
    features = [average_pitch, average_duration]

    # notes durations and density (4 features)
    durations = [note.end - note.start for note in notes]
    # std_dev_duration = np.std(durations)
    short_threshold = 0.5 * np.mean(durations)  # or use a fixed value like 0.25
    short_notes = [d for d in durations if d < short_threshold]
    short_note_ratio = len(short_notes) / len(durations)
    mid = MidiFile(dataroot1 + path)
    # total_time = mid.length  # in seconds
    note_density = num_notes / mid.length
    features.extend([short_note_ratio, note_density])

    # Volume (3 features)
    velocities = np.array([note.velocity for note in notes])
    average_velocity = np.mean(velocities)
    # median_velocity = np.median(velocities)
    std_dev_velocity = np.std(velocities)
    velocity_range = np.max(velocities) - np.min(velocities)
    features.extend([average_velocity, std_dev_velocity, velocity_range])

    # Get tempo changes (3 features)
    tempo_changes = midi_obj.tempo_changes
    tempo_values = []
    tempo_times = []
    average_tempo = 120 # Default tempo if no tempo changes
    std_dev_tempo = 0
    # num_tempo_changes = 0
    if tempo_changes:
        # Sort tempo changes by time
        tempo_changes.sort(key=lambda x: x.time)
        tempo_values = np.array([tempo.tempo for tempo in tempo_changes])
        tempo_times = np.array([tempo.time for tempo in tempo_changes])

        average_tempo = np.mean(tempo_values)
        std_dev_tempo = np.std(tempo_values)
        # num_tempo_changes = len(tempo_values)
    features.extend([average_tempo, std_dev_tempo])

    # Register (5 features)
    pitches = np.array([note.pitch for note in notes])
    pitch_range = np.max(pitches) - np.min(pitches) if pitches.size > 0 else 0
    pitch_max = np.max(pitches)
    high_notes = [note for note in notes if note.pitch > 72]  # use 72 (C5, one octave above middle C) as a threshold
    high_register_ratio = len(high_notes) / len(notes)
    pitches_std_dev = np.std(pitches)
    # pitch_mad = np.mean([abs(p - average_pitch) for p in pitches])
    features.extend([pitch_range, pitches_std_dev, pitch_max, high_register_ratio])

    # Left and right hands (2 features)
    # Standard deviation of the highest and lowest 35% of notes (as a proxy for right hand and left hand melodies)
    # Idk how accurate this is but let's try, I feel like if std dev is lower then it might be like Mozart and
    # if it's higher it might be Chopin or smth
    percentile = 35
    sorted_pitches = np.sort(pitches)
    num_highest_and_lowest_notes = int(np.ceil(num_notes * (percentile / 100)))
    highest_pitches = sorted_pitches[-num_highest_and_lowest_notes:]
    lowest_pitches = sorted_pitches[:num_highest_and_lowest_notes]
    # highest_notes_std_dev = np.std(highest_pitches)
    # lowest_notes_std_dev = np.std(lowest_pitches)
    highest_notes_range = np.max(highest_pitches) - np.min(highest_pitches)
    lowest_notes_range = np.max(lowest_pitches) - np.min(lowest_pitches)
    features.extend([highest_notes_range, lowest_notes_range])

    # melodic pitch jumps (2 features)
    # notes_sorted = sorted(notes, key=lambda n: (n.start, -n.pitch))  # -pitch to get highest first
    time_to_highest = {}  # get highest note at each time step
    for note in notes:
        if note.start not in time_to_highest or note.pitch > time_to_highest[note.start]:
            time_to_highest[note.start] = note.pitch
    melody_pitches = [time_to_highest[t] for t in sorted(time_to_highest)]
    pitch_jumps = [abs(melody_pitches[i+1] - melody_pitches[i]) for i in range(len(melody_pitches) - 1)]
    avg_jump = np.mean(pitch_jumps)
    std_jump = np.std(pitch_jumps)
    # max_jump = np.max(pitch_jumps)
    features.extend([avg_jump, std_jump])

    # Pitch classes
    pitch_class_counts = [0] * 12
    for note in notes:
        pc = note.pitch % 12
        pitch_class_counts[pc] += 1
    pitch_class_distribution = [count / len(notes) for count in pitch_class_counts]
    features.extend(pitch_class_distribution)

    # direction changes (1 feature)
    diffs = [melody_pitches[i+1] - melody_pitches[i] for i in range(len(melody_pitches) - 1)]
    directions = [np.sign(d) for d in diffs]
    direction_changes = sum(
        1 for i in range(len(directions) - 1) if directions[i] != 0 and directions[i] != directions[i+1]
    )
    direction_changes_ratio = direction_changes / (len(directions) - 1) if len(directions) > 1 else 0.0
    features.extend([direction_changes_ratio])

    # Time signature (1 feature)
    tsig_count = len(midi_obj.time_signature_changes)
    features.extend([tsig_count])

    # Autocorrelation
    velocities = [note.velocity for note in notes]
    v = np.array(velocities)
    v_mean = np.mean(v)
    numerator = np.sum((v[:-1] - v_mean) * (v[1:] - v_mean))
    denominator = np.sum((v - v_mean) ** 2)
    vel_autocorr_1 = numerator / denominator if denominator != 0 else 0.0
    features.extend([vel_autocorr_1])

    # IOI (inter-onset interval)
    onsets = sorted([note.start for note in notes])
    ioi = np.diff(onsets)  # list of time differences between consecutive onsets
    ioi_mean = np.mean(ioi)
    ioi_std = np.std(ioi)
    features.extend([ioi_mean, ioi_std])

    # Unique pitch ratio
    unique_pitches = len(set(pitches))
    unique_pitch_ratio = unique_pitches / len(pitches)
    features.extend([unique_pitch_ratio])

    # octave bins
    bins = np.arange(0, 128, 12)  # bins edges: 0, 12, 24, ..., 120
    octave_hist, _ = np.histogram(pitches, bins=bins)
    octave_hist = octave_hist / octave_hist.sum()  # normalize
    features.extend(octave_hist.tolist())

    # rhythmic entropy
    if len(ioi) > 1:
        # Bin IOIs (rounding helps reduce noise and collapse near-duplicates)
        binned_ioi = np.round(ioi, decimals=3)  # Adjust bin size as needed
        values, counts = np.unique(binned_ioi, return_counts=True)
        probs = counts / counts.sum()
        rhythmic_entropy = -np.sum(probs * np.log2(probs))
    else:
        rhythmic_entropy = 0.0  # No rhythm to analyze
    features.extend([rhythmic_entropy])

    return features

# Training data
train_path = dataroot1 + "/train.json"

with open(train_path, 'r') as f:
    train_json = eval(f.read())

files_train_all = [k for k in train_json]
X_train_all = [features(k) for k in train_json]
y_train_all = [train_json[k] for k in train_json]

X_train, X_val, y_train, y_val, files_train, files_val = train_test_split(
        X_train_all, y_train_all, files_train_all, test_size=0.2, shuffle=True, random_state=42
    )

print(files_train_all[5])
print(X_train_all[5])
print(y_train_all[5])

print(files_train[5])
print(X_train[5])
print(y_train[5])

print(files_val[5])
print(X_val[5])
print(y_val[5])

groundtruth_train_all = {k: train_json[k] for k in train_json}
groundtruth_train = {k: train_json[k] for k in files_train}
groundtruth_val = {k: train_json[k] for k in files_val}

# Testing data
test_path = dataroot1 + "/test.json"

d = eval(open(test_path, 'r').read())

files_test = []
X_test = []
for k in d:
    files_test.append(k)
    X_test.append(features(k))

print(files_test[5])
print(X_test[5])

"""# Eval Function"""

def accuracy1(groundtruth: dict, predictions):
    correct = 0
    for k in groundtruth:
        if not (k in predictions):
            print("Missing " + str(k) + " from predictions")
            return 0
        if predictions[k] == groundtruth[k]:
            correct += 1
    print(correct, len(groundtruth), correct / len(groundtruth))
    return correct / len(groundtruth)

"""# Model: Autogluon"""

!rm -rf AutogluonModels

import multiprocessing
num_cores = multiprocessing.cpu_count()

# hyperparameters={'GBM': {}, 'RF': {}}
hyperparams = {
    'XGB': {},         # XGBoost
    'GBM': {},         # LightGBM (AutoGluon's GBM refers to LightGBM)
    'CAT': {},         # CatBoost
    'RF': {},          # Random Forest
    # 'XT': {},          # Extra Trees
    'GBC': {}          # sklearn.ensemble.GradientBoostingClassifier
}

class AutoGluon():
    def __init__(self):
        self.label = 'label'

    def predict(self, files, features, outpath=None):
        df = pd.DataFrame(features)
        preds = self.model.predict(df)
        predictions = {}
        for i, k in enumerate(files):
            predictions[k] = str(preds.iloc[i])
        if outpath:
            with open(outpath, "w") as z:
                z.write(str(predictions) + '\n')
        return predictions

    def train(self, X_train, y_train):
        df_train = pd.DataFrame(X_train)
        df_train[self.label] = y_train
        # also try best_quality if there's time
        self.model = TabularPredictor(label=self.label).fit(
            df_train,
            presets='high_quality',
            num_cpus=num_cores,
            ag_args_fit={'num_gpus': 1},
            # verbosity=4,
            ag_args_ensemble =dict(fold_fitting_strategy='sequential_local'),  # disable Ray
            save_bag_folds=True
        )  # , num_cpus=num_cores

!rm predictions1.json
ag = AutoGluon()
ag.train(X_train_all, y_train_all)

df_train = pd.DataFrame(X_train_all)
df_train[ag.label] = y_train_all
ag.model.leaderboard(df_train)

train_preds_all = ag.predict(files_train_all, X_train_all)
val_preds = ag.predict(files_val, X_val)
test_preds = ag.predict(files_test, X_test, "predictions1.json")

acc1_train = accuracy1(groundtruth_train_all, train_preds_all)
acc1_val = accuracy1(groundtruth_val, val_preds)
print("Task 3 training accuracy = " + str(acc1_train))
print("Task 3 validation accuracy = " + str(acc1_val))


# -------------- task 2 --------------

# -*- coding: utf-8 -*-
"""task2.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1thiTD7HHROsidTwoGO2CSIagujyGJddw

Midi file player https://midiplayer.ehubsoft.net/

# Setup
"""

!curl -L https://cseweb.ucsd.edu/classes/sp25/cse253-a/data/student_files.tar.gz -o student_files.tar.gz

# !rm student_files.tar.gz

# Make sure to wait until the file fully finishes uploading
# !rm -rf student_files
!tar -xvzf student_files.tar.gz | tail -n 5  # Make sure there's no error message at the tail

!pip install librosa | tail -n 1  # I don't want a super long output
!pip install miditoolkit | tail -n 1
!pip install xgboost | tail -n 1
!pip install lightgbm | tail -n 1

# Probably more imports than are really necessary...
import os
import torch
import torchaudio
from torch.utils.data import Dataset, DataLoader, random_split
import torch.nn as nn
import torch.nn.functional as F
from torchaudio.transforms import MelSpectrogram, AmplitudeToDB
from tqdm import tqdm
import librosa
import numpy as np
import miditoolkit
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, average_precision_score, accuracy_score
import random

from mido import MidiFile
from sklearn.model_selection import train_test_split
from music21 import converter, chord, stream
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBClassifier
from sklearn.ensemble import GradientBoostingClassifier
from lightgbm import LGBMClassifier

"""# Getting data"""

dataroot2 = "student_files/task2_next_sequence_prediction/"

"""### Current Solution"""

# from collections import Counter

# def extract_chord(notes):
#     if not notes:
#         return ()

#     pitch_classes = [note.pitch % 12 for note in notes]
#     pc_counter = Counter(pitch_classes)

#     # Select the top 3 most common pitch classes (simple chord proxy)
#     top_3 = [pc for pc, _ in pc_counter.most_common(3)]
#     return tuple(sorted(top_3))  # e.g., (0, 4, 7) = C major

def features2(path):
    full_path = dataroot2 + 'midis/' + path
    midi_obj = miditoolkit.midi.parser.MidiFile(full_path)

    notes = midi_obj.instruments[0].notes

    abs_features = []  # features to take the absolute value of between two midi files

    # tempo
    mean_tempo = 120.0  # default
    tempo_std = 0.0  # default
    tempos = midi_obj.tempo_changes
    if tempos:
        tempo_values = [t.tempo for t in tempos]
        mean_tempo = np.mean(tempo_values)
        tempo_std = np.std(tempo_values)
    abs_features.extend([mean_tempo, tempo_std])

    # time signature
    ts_numerator = 4  # default
    ts_denominator = 4
    tsigs = midi_obj.time_signature_changes
    if tsigs:
        ts_numerator = tsigs[0].numerator
        ts_denominator = tsigs[0].denominator
    abs_features.extend([ts_numerator, ts_denominator])

    # intervals
    time_to_highest = {}  # get highest note at each time step (melody)
    time_to_highest_vel = {}  # not for this feature, but will come in handy later
    for note in notes:
        if note.start not in time_to_highest or note.pitch > time_to_highest[note.start]:
            time_to_highest[note.start] = note.pitch
            time_to_highest_vel[note.start] = note.velocity
    melody_pitches = [time_to_highest[t] for t in sorted(time_to_highest)]
    melody_velocities = [time_to_highest_vel[t] for t in sorted(time_to_highest_vel)]  # not for this feature, but will come in handy later
    intervals = [abs(melody_pitches[i+1] - melody_pitches[i]) for i in range(len(melody_pitches) - 1)]
    avg_interval = 0  # default
    intervals_std = 0
    if len(intervals) > 0:
      avg_interval = np.mean(intervals)
      intervals_std = np.std(intervals)
    abs_features.extend([avg_interval, intervals_std])

    # ioi
    onsets = sorted([note.start for note in notes])
    ioi = np.diff(onsets)
    ioi_mean = 0  # default
    ioi_std = 0
    if len(ioi) > 0:
      ioi_mean = np.mean(ioi)
      ioi_std = np.std(ioi)
    abs_features.extend([ioi_mean, ioi_std])

    # average duration of left and right hand
    sorted_by_pitch = sorted(notes, key=lambda n: n.pitch)
    num_notes = len(sorted_by_pitch)
    # cutoff = int(np.ceil(num_notes * 0.35))  # use 35% as a proxy for left and right hand notes
    # left_notes = sorted_by_pitch[:cutoff]
    # right_notes = sorted_by_pitch[-cutoff:]
    # left_durations = [note.end - note.start for note in left_notes]
    # right_durations = [note.end - note.start for note in right_notes]
    # left_avg_duration = np.mean(left_durations) if left_durations else 0
    # right_avg_duration = np.mean(right_durations) if right_durations else 0
    # abs_features.extend([left_avg_duration, right_avg_duration])

    # note density
    # mid = MidiFile(full_path)
    # total_time = mid.length  # in seconds
    # note_density = num_notes / total_time
    # abs_features.extend([note_density])

    # pitch classes
    pitch_class_counts = [0] * 12
    for note in notes:
        pc = note.pitch % 12
        pitch_class_counts[pc] += 1
    pitch_class_distribution = [count / len(notes) for count in pitch_class_counts]
    abs_features.extend(pitch_class_distribution)

    # # ticks per beat
    # abs_features.extend([midi_obj.ticks_per_beat])

    # first and last pitches
    first_pitch = melody_pitches[0]
    last_pitch = melody_pitches[-1]

    # first and last tempos
    tempo_changes = midi_obj.tempo_changes
    first_tempo = tempo_changes[0].tempo if midi_obj.tempo_changes else 120.0 # Default if no tempo changes
    last_tempo = tempo_changes[-1].tempo if midi_obj.tempo_changes else 120.0

    # first and last velocities
    first_velocity = 0  # default
    last_velocity = 0
    if melody_velocities:
      first_velocity = melody_velocities[0]
      last_velocity = melody_velocities[-1]

    # first and last time signatures
    first_numerator = last_numerator = 4  # default
    first_denominator = last_denominator = 4
    if midi_obj.time_signature_changes:
      time_sigs = midi_obj.time_signature_changes
      time_sigs.sort(key=lambda x: x.time)
      first_ts = time_sigs[0]
      last_ts = time_sigs[-1]
      first_numerator = first_ts.numerator
      first_denominator = first_ts.denominator
      last_numerator = last_ts.numerator
      last_denominator = last_ts.denominator

    return (abs_features, first_pitch, last_pitch, first_tempo, last_tempo, first_velocity, last_velocity,
            first_numerator, first_denominator, last_numerator, last_denominator)

def combineFeatures(features_1, features_2):
    # features has format (0 abs_features, 1 first_pitch, 2 last_pitch, 3 first_tempo,
    # 4 last_tempo, 5 first_velocity, 6 last_velocity, 7 first_numerator, 8 first_denominator,
    # 9 last_numerator, 10 last_denominator)
    combined_features = [abs(x - y) for x, y in zip(features_1[0], features_2[0])]

    # abs(last_pitch_1 - first_pitch_2)
    last_pitch_1 = features_1[2]
    first_pitch_2 = features_2[1]
    interval = abs(last_pitch_1 - first_pitch_2)
    combined_features.append(interval)

    # how many standard deviations is this interval off from the mean interval for both midi files?
    z_1 = (interval - features_1[0][4]) / features_1[0][5] if features_1[0][5] > 0 else 0
    z_2 = (interval - features_2[0][4]) / features_2[0][5] if features_2[0][5] > 0 else 0
    combined_features.extend([z_1, z_2])

    # abs(end_tempo_1 - start_tempo_2)
    end_tempo_1 = features_1[4]
    start_tempo_2 = features_2[3]
    combined_features.append(abs(start_tempo_2 - end_tempo_1))

    # velocity
    end_velocity_1 = features_1[6]
    start_velocity_2 = features_2[5]
    combined_features.append(abs(end_velocity_1 - start_velocity_2))

    # time signature
    end_numerator_1 = features_1[9]
    start_numerator_2 = features_2[7]
    combined_features.append(abs(end_numerator_1 - start_numerator_2))
    end_denominator_1 = features_1[10]
    start_denominator_2 = features_2[8]
    combined_features.append(abs(end_denominator_1 - start_denominator_2))

    return combined_features

# We want to only get feature vectors once since they're costly, so save them in a dictionary
features = {}
midi_dir = dataroot2 + "midis/"
for f in os.listdir(midi_dir):
    features["midis/" + f] = features2(f)

# Training data
train_path = dataroot2 + "train.json"

with open(train_path, 'r') as f:
    train_json = eval(f.read())

file_pairs_train_all = [k for k in train_json]
X_train_all = [combineFeatures(features[k1], features[k2]) for (k1, k2) in file_pairs_train_all]
y_train_all = [train_json[k] for k in train_json]

X_train, X_val, y_train, y_val, file_pairs_train, file_pairs_val = train_test_split(
        X_train_all, y_train_all, file_pairs_train_all, test_size=0.2, random_state=42, shuffle=True
    )

print(file_pairs_train_all[5])
print(X_train_all[5])
print(y_train_all[5])

print(file_pairs_train[5])
print(X_train[5])
print(y_train[5])

print(file_pairs_val[5])
print(X_val[5])
print(y_val[5])

groundtruth_train_all = {k: train_json[k] for k in train_json}
groundtruth_train = {k: train_json[k] for k in file_pairs_train}
groundtruth_val = {k: train_json[k] for k in file_pairs_val}

# Testing data
test_path = dataroot2 + "test.json"

d = eval(open(test_path, 'r').read())

file_pairs_test = [k for k in d]
X_test = [combineFeatures(features[k1], features[k2]) for (k1, k2) in file_pairs_test]

print(file_pairs_test[5])
print(X_test[5])

"""# Eval Function"""

def accuracy2(groundtruth, predictions):
    correct = 0
    for k in groundtruth:
        if not (k in predictions):
            print("Missing " + str(k) + " from predictions")
            return 0
        if predictions[k] == groundtruth[k]:
            correct += 1
    return correct / len(groundtruth)

"""# Model: Logistic Regression

https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LogisticRegression.html
"""

class logisticReg():
    def __init__(self):
        pass

    def train(self, X_train, y_train):
        model = LogisticRegression(max_iter=5000, random_state=42)
        model.fit(X_train, y_train)
        self.model = model

    def predict(self, file_pairs, features, outpath=None):
        predictions = {}
        for i, x in enumerate(features):
            k = file_pairs[i]
            pred = self.model.predict([x])
            predictions[k] = bool(pred[0])  # pred is [True] or [False]
            # print(k, pred)
        if outpath:
            with open(outpath, "w") as z:
                z.write(str(predictions) + '\n')
        return predictions

def runLogisticRegression():
    model = logisticReg()
    model.train(X_train, y_train)
    train_preds = model.predict(file_pairs_train, X_train)
    val_preds = model.predict(file_pairs_val, X_val)
    test_preds = model.predict(file_pairs_test, X_test, "predictions2.json")

    acc1_train = accuracy2(groundtruth_train, train_preds)
    acc1_val = accuracy2(groundtruth_val, val_preds)
    print("Task 1 training accuracy = " + str(acc1_train))
    print("Task 1 validation accuracy = " + str(acc1_val))

!rm predictions2.json
runLogisticRegression()

"""# Model: XGBoost"""

class XGBoost():
    def __init__(self):
        pass

    def predict(self, files, features, outpath=None):
        predictions = {}
        for i, x in enumerate(features):
            k = files[i]
            pred = self.model.predict([x])
            pred = self.label_encoder.inverse_transform(pred)
            predictions[k] = bool(pred[0])
            # print(k, pred[0])
        if outpath:
            with open(outpath, "w") as z:
                z.write(str(predictions) + '\n')
        return predictions

    def train(self, X_train, y_train):
        # XGBoost wants outputs in [0 1] instead of [True False]
        # So we need to encode boolean labels to numerical values
        self.label_encoder = LabelEncoder()
        y_train_encoded = self.label_encoder.fit_transform(y_train)
        print(f"Classes found by LabelEncoder (should be 2): {self.label_encoder.classes_}")
        model = XGBClassifier(n_estimators=500, max_depth=15, learning_rate=0.1, objective='binary:logistic')
        model.fit(X_train, y_train_encoded)
        self.model = model

def runXGBoost():
    model = XGBoost()
    model.train(X_train_all, y_train_all)
    train_preds = model.predict(file_pairs_train, X_train)
    val_preds = model.predict(file_pairs_val, X_val)
    test_preds = model.predict(file_pairs_test, X_test, "predictions2.json")

    acc1_train = accuracy2(groundtruth_train, train_preds)
    acc1_val = accuracy2(groundtruth_val, val_preds)
    print("Task 1 training accuracy = " + str(acc1_train))
    print("Task 1 validation accuracy = " + str(acc1_val))

!rm predictions2.json
runXGBoost()


# -------------- task 3 --------------

#!/usr/bin/env python
# coding: utf-8

# Midi file player
# https://midiplayer.ehubsoft.net/


# # Model: AST Attempt 2

# In[1]:


import torch
import torch.nn as nn
import torch.nn.functional as F
import torchaudio
import librosa
import numpy as np
from torch.utils.data import Dataset, DataLoader, random_split
from torchaudio.transforms import MelSpectrogram, AmplitudeToDB
from tqdm import tqdm
import random
from sklearn.metrics import average_precision_score
from transformers import ASTConfig, AutoFeatureExtractor, ASTForAudioClassification


# In[2]:


device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
print(device)


# In[3]:


# Some constants (you can change any of these if useful)
SAMPLE_RATE = 16000
N_MELS = 64
N_CLASSES = 10  # This should be inferred from your TAGS list or dataset
AUDIO_DURATION = 10 # seconds
BATCH_SIZE = 4  # From this piazza post: https://piazza.com/class/m8rskujtdvsgy/post/340
EPOCHS = 8  # 10 From Discord
LEARNING_RATE = 1e-5  # From Discord


# In[4]:


dataroot3 = "student_files/task3_audio_classification/"


# In[5]:


TAGS = ['rock', 'oldies', 'jazz', 'pop', 'dance',  'blues',  'punk', 'chill', 'electronic', 'country']


# In[6]:


def accuracy3(groundtruth, predictions):
    preds, targets = [], []
    for k in groundtruth:
        if not (k in predictions):
            print("Missing " + str(k) + " from predictions")
            return 0
        prediction = [1 if tag in predictions[k] else 0 for tag in TAGS]
        target = [1 if tag in groundtruth[k] else 0 for tag in TAGS]
        preds.append(prediction)
        targets.append(target)

    mAP = average_precision_score(targets, preds, average='macro')
    return mAP


# In[7]:


def extract_waveform(path, augment=False):
    w, sr = librosa.load(dataroot3 + '/' + path, sr=SAMPLE_RATE)

    waveform = np.array([w])
    
    if sr != SAMPLE_RATE:
        resample = torchaudio.transforms.Resample(orig_freq=sr, new_freq=SAMPLE_RATE)
        waveform = resample(waveform)
    # Pad so that everything is the right length
    target_len = SAMPLE_RATE * AUDIO_DURATION
    waveform = torch.from_numpy(waveform)
    if waveform.shape[1] < target_len:
        pad_len = target_len - waveform.shape[1]
        waveform = F.pad(waveform, (0, pad_len))
    else:
        waveform = waveform[:, :target_len]
        
    waveform = torch.FloatTensor(waveform)

    if not augment:
        return waveform

    # Data augmentation
    rng = random.randint(0, 2)
    if rng == 0:
        pitch_shift_steps = random.choice([i for i in range(-11, 12) if i != 0])  ## exclude 0 (no pitch change) 
        augmented_waveform = librosa.effects.pitch_shift(w, sr=sr, n_steps=pitch_shift_steps)
    elif rng == 1:
        speed_factor = random.uniform(0.5, 1.5)
        augmented_waveform = librosa.effects.time_stretch(y=w, rate=speed_factor)
    elif rng == 2:
        volume_factor = random.uniform(0.5, 2.0)
        augmented_waveform = w * volume_factor
        # Clip values to prevent distortion/clipping if volume becomes too high
        # Audio typically ranges from -1.0 to 1.0 (float32)
        augmented_waveform = np.clip(augmented_waveform, -1.0, 1.0)

    # Pad so that everything is the right length
    augmented_waveform = np.array([w])
    augmented_waveform = torch.from_numpy(augmented_waveform)
    if augmented_waveform.shape[1] < target_len:
        pad_len = target_len - augmented_waveform.shape[1]
        augmented_waveform = F.pad(augmented_waveform, (0, pad_len))
    else:
        augmented_waveform = augmented_waveform[:, :target_len]
        
    augmented_waveform = torch.FloatTensor(augmented_waveform)

    return (waveform, augmented_waveform)


# In[8]:


# def extract_waveform_pitch_shift(path):
#     waveform, sr = librosa.load(dataroot3 + '/' + path, sr=SAMPLE_RATE)

#     pitch_shift_steps = random.choice([i for i in range(-11, 12) if i != 0])  ## exclude 0 (no pitch change) 
#     waveform = librosa.effects.pitch_shift(waveform, sr=sr, n_steps=pitch_shift_steps)
    
#     waveform = np.array([waveform])
    
#     waveform = torch.FloatTensor(waveform)
    
#     return waveform


# In[9]:


# def extract_waveform_speed_change(path):
#     waveform, sr = librosa.load(dataroot3 + '/' + path, sr=SAMPLE_RATE)
    
#     speed_factor = random.uniform(0.5, 1.5)
#     waveform = librosa.effects.time_stretch(y=waveform, rate=speed_factor)

#     waveform = np.array([waveform])
    
#     waveform = torch.FloatTensor(waveform)
    
#     return waveform


# In[10]:


# def extract_waveform_volume_change(path):
#     waveform, sr = librosa.load(dataroot3 + '/' + path, sr=SAMPLE_RATE)

#     volume_factor = random.uniform(0.5, 2.0)
#     waveform = waveform * volume_factor
#     # Clip values to prevent distortion/clipping if volume becomes too high
#     # Audio typically ranges from -1.0 to 1.0 (float32)
#     augmented_waveform = np.clip(waveform, -1.0, 1.0)

#     waveform = np.array([waveform])
    
#     waveform = torch.FloatTensor(waveform)
    
#     return waveform


# In[11]:


# config = ASTConfig(num_labels=N_CLASSES)


# In[12]:


feature_extractor = AutoFeatureExtractor.from_pretrained("MIT/ast-finetuned-audioset-10-10-0.4593")
pretrained_model = ASTForAudioClassification.from_pretrained("MIT/ast-finetuned-audioset-10-10-0.4593")


# In[13]:


# note: the code doesn't work if preload is false

class AudioDataset(Dataset):
    def __init__(self, meta, feature_extractor, augment, preload=True):
        self.meta = meta
        print(len(meta))
        ks = list(meta.keys())
        self.idToPath = dict(zip(range(len(ks)), ks))
        self.pathToFeat = {}
        self.feature_extractor = feature_extractor
        self.preload = preload

        if self.preload:
            idx_augment = len(ks)
            for path in ks:
                # waveform = extract_waveform(path)
                # features = self.feature_extractor(waveform.squeeze().numpy(), sampling_rate=SAMPLE_RATE, return_tensors="pt")
                # self.pathToFeat[path] = features['input_values'].squeeze(0)

                rng = random.randint(0, 3)
                if augment and rng == 0:
                    print(idx_augment)
                    # A quarter of a chance of data being augmented
                    waveforms = extract_waveform(path, augment=True)
                    
                    waveform = waveforms[0]
                    features = self.feature_extractor(waveform.squeeze().numpy(), sampling_rate=SAMPLE_RATE, return_tensors="pt")
                    self.pathToFeat[path] = features['input_values'].squeeze(0)
                    
                    augmented_waveform = waveforms[1]
                    features = self.feature_extractor(augmented_waveform.squeeze().numpy(), sampling_rate=SAMPLE_RATE, return_tensors="pt")
                    self.pathToFeat[path + "_augment"] = features['input_values'].squeeze(0)
                    self.idToPath[idx_augment] = path + "_augment"
                    self.meta[path + "_augment"] = self.meta[path]
                    idx_augment += 1
                else:
                    waveform = extract_waveform(path)
                    features = self.feature_extractor(waveform.squeeze().numpy(), sampling_rate=SAMPLE_RATE, return_tensors="pt")
                    self.pathToFeat[path] = features['input_values'].squeeze(0)

            if augment:
                idx_augment = len(ks)
                for path in ks:
                    
                    idx_augment += 1

    def __len__(self):
        return len(self.meta)

    def __getitem__(self, idx):
        path = self.idToPath[idx]
        tags = self.meta[path]
        bin_label = torch.tensor([1 if tag in tags else 0 for tag in TAGS], dtype=torch.float32)

        if self.preload:
            features = self.pathToFeat[path]
        else:
            waveform = extract_waveform(path)
            features = self.feature_extractor(waveform.squeeze().numpy(), sampling_rate=SAMPLE_RATE, return_tensors="pt")
            features = features['input_values'].squeeze(0)

        return features, bin_label, path


# In[14]:


class Loaders():
    def __init__(self, train_path, test_path, feature_extractor, split_ratio=0.9, seed=0):
        torch.manual_seed(seed)
        random.seed(seed)

        meta_train = eval(open(train_path, 'r').read())
        l_test = eval(open(test_path, 'r').read())
        meta_test = dict([(x, []) for x in l_test])

        all_train = AudioDataset(meta_train, feature_extractor, augment=True)
        test_set = AudioDataset(meta_test, feature_extractor, augment=False)

        total_len = len(all_train)
        train_len = int(total_len * split_ratio)
        valid_len = total_len - train_len
        train_set, valid_set = random_split(all_train, [train_len, valid_len])

        self.loaderTrain = DataLoader(train_set, batch_size=BATCH_SIZE, shuffle=False, num_workers=0)
        self.loaderValid = DataLoader(valid_set, batch_size=BATCH_SIZE, shuffle=False, num_workers=0)
        self.loaderTest = DataLoader(test_set, batch_size=BATCH_SIZE, shuffle=False, num_workers=0)


# In[15]:


class ASTForMultiLabel(nn.Module):
    def __init__(self, base_model, num_labels):
        super().__init__()
        self.base_model = base_model  # e.g. ASTForAudioClassification.from_pretrained(...)
        # self.dropout = nn.Dropout(0.3)
        self.classifier = nn.Linear(527, num_labels)
        nn.init.xavier_uniform_(self.classifier.weight)

    def forward(self, input_values):
        outputs = self.base_model(input_values).logits  # shape: [B, 527]
        # outputs = self.dropout(outputs)
        outputs = self.classifier(outputs)              # shape: [B, num_labels]
        return torch.sigmoid(outputs)                   # For multi-label classification


# In[16]:


class Pipeline():
    def __init__(self, model, learning_rate, seed=0):
        torch.manual_seed(seed)
        random.seed(seed)

        self.device = device
        self.model = model.to(self.device)
        self.optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
        self.criterion = nn.BCELoss()

    def evaluate(self, loader, threshold=0.5, outpath=None):
        print("evaluating")
        self.model.eval()
        preds, targets, paths = [], [], []
        with torch.no_grad():
            for x, y, ps in loader:
                x = x.to(self.device)
                y = y.to(self.device)
                outputs = self.model(x)
                preds.append(outputs.cpu())
                targets.append(y.cpu())
                paths += list(ps)

        preds = torch.cat(preds)
        targets = torch.cat(targets)
        preds_bin = (preds > threshold).float()

        predictions = {}
        for i in range(preds_bin.shape[0]):
            predictions[paths[i]] = [TAGS[j] for j in range(len(preds_bin[i])) if preds_bin[i][j]]

        mAP = None
        if outpath:
            with open(outpath, "w") as z:
                z.write(str(predictions) + '\n')
        else:
            mAP = average_precision_score(targets, preds, average='macro')
        return predictions, mAP

    def train(self, train_loader, val_loader, num_epochs):
        print("training")
        for epoch in range(num_epochs):
            self.model.train()
            running_loss = 0.0
            for x, y, path in tqdm(train_loader, desc=f"Epoch {epoch+1}"):
                x = x.to(self.device)
                y = y.to(self.device)
                self.optimizer.zero_grad()
                outputs = self.model(x)
                loss = self.criterion(outputs, y)
                loss.backward()
                self.optimizer.step()
                running_loss += loss.item()
            val_predictions, mAP = self.evaluate(val_loader)
            print(f"[Epoch {epoch+1}] Loss: {running_loss/len(train_loader):.4f} | Val mAP: {mAP:.4f}")


# In[17]:


def accuracy3(all_train_meta, train_preds):
    correct_predictions = 0
    total_predictions = 0
    for audio_file, true_tags in all_train_meta.items():
        if audio_file in train_preds:
            predicted_tags = set(train_preds[audio_file])
            true_tags_set = set(true_tags)
            if predicted_tags == true_tags_set:
                correct_predictions += 1
            total_predictions += 1
    if total_predictions > 0:
        return correct_predictions / total_predictions
    else:
        return 0.0


# In[18]:


# def run3():
#     print("here 1")
#     loaders = Loaders(dataroot3 + "/train.json", dataroot3 + "/test.json", feature_extractor)
#     print("here 2")
#     model = ASTForMultiLabel(pretrained_model, N_CLASSES)
#     print("here 3")
#     pipeline = Pipeline(model, LEARNING_RATE)
#     print("here 4")

#     pipeline.train(loaders.loaderTrain, loaders.loaderValid, EPOCHS)
#     train_preds, train_mAP = pipeline.evaluate(loaders.loaderTrain, 0.5)
#     valid_preds, valid_mAP = pipeline.evaluate(loaders.loaderValid, 0.5)
#     test_preds, _ = pipeline.evaluate(loaders.loaderTest, 0.5, "predictions3.json")

#     all_train = eval(open(dataroot3 + "/train.json").read())
#     for k in valid_preds:
#         if k in all_train: # Ensure the key exists before trying to pop
#             all_train.pop(k)
#     acc3 = accuracy3(all_train, train_preds)
#     print("Task 3 training accuracy (exact match) = " + str(acc3))
#     print("Task 3 training mAP = " + str(train_mAP))
#     print("Task 3 validation mAP = " + str(valid_mAP))


# In[19]:


# !rm predictions3.json
# run3()


# In[ ]:


print("here 1")
loaders = Loaders(dataroot3 + "/train.json", dataroot3 + "/test.json", feature_extractor)
# print("here 2")
# model = ASTForMultiLabel(pretrained_model, N_CLASSES)
# print("here 3")
# pipeline = Pipeline(model, LEARNING_RATE)
# print("here 4")
# pipeline.train(loaders.loaderTrain, loaders.loaderValid, EPOCHS)


# In[ ]:


torch.save(model, "trained_ast_model.pth")
print(f"Trained model saved to: trained_ast_model.pth")


# In[ ]:


# model = torch.load("ast_model_full.pth")


# In[ ]:


train_preds, train_mAP = pipeline.evaluate(loaders.loaderTrain, 0.5)
valid_preds, valid_mAP = pipeline.evaluate(loaders.loaderValid, 0.5)
test_preds, _ = pipeline.evaluate(loaders.loaderTest, 0.5, "predictions3.json")

all_train = eval(open(dataroot3 + "/train.json").read())
for k in valid_preds:
    if k in all_train: # Ensure the key exists before trying to pop
        all_train.pop(k)
acc3 = accuracy3(all_train, train_preds)
print("Task 3 training accuracy (exact match) = " + str(acc3))
print("Task 3 training mAP = " + str(train_mAP))
print("Task 3 validation mAP = " + str(valid_mAP))
