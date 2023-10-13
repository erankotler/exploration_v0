# V1 of microscope for preliminary analysis of DNAm datasets
# Goal is to get rough estimation of classification/prediction accuracy in any processed DNAm dataset
# This module contains:
    # data object setup (data, metadata, targets)
    # processing for regression/classification (NaN policy, matrix organization)
    # preliminary EDA (vizualization, pCA, etc)
    # functions for simple model training (l1, l2, elastic net, gradient boosting)
    # functions for inference/prediction using pretrained models
    # result vizualization
    # Methods for saving models and results


# Eran Kotler
# Last updated: 2023-10-08



### Fucntion imports
# ==================
import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import random
import pickle
from itertools import compress
from scipy import stats
from multiprocessing import Pool
import timeit
from datetime import datetime
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import KFold, LeaveOneOut, train_test_split
from sklearn.linear_model import LogisticRegressionCV
from sklearn import metrics
from sklearn.metrics import classification_report
from sklearn.metrics import roc_auc_score, roc_curve, accuracy_score, balanced_accuracy_score


### DNAm data object
# ==================
class Dataset():
    """ Main class for DNAm data, to be used for model training/testing"""
    def __init__(self, gse_d, data_type="array"):
        self.gse_d=gse_d
        self.data_type=data_type
        self.mat=None
        self.groups=None
        self.pheno=None

    def load_data(self, max_CpGs=None, max_samps=None):
        # Load raw DNAm data and metadata into object
        if max_samps is None:
            self.mat = pd.read_csv(os.path.join(self.gse_d, "matrix_beta.tsv"), index_col=0, sep="\t", nrows=max_CpGs)
        else:
            try:
                self.mat = pd.read_csv(os.path.join(self.gse_d, "matrix_beta.tsv"), index_col=0, sep="\t", nrows=max_CpGs, usecols=list(range(max_samps+1)))
            except:
                print("Unable to load requested number of samples, loading entire dataset")
                self.mat = pd.read_csv(os.path.join(self.gse_d, "matrix_beta.tsv"), index_col=0, sep="\t", nrows=max_CpGs)
        
        if "ID_REF" in self.mat.columns: # fix for datasets where ID_REF is loaded as a column
            self.mat.index = self.mat["ID_REF"]
            self.mat = self.mat.drop("ID_REF", axis=1)
            
        self.groups = pd.read_csv(os.path.join(self.gse_d, "groups.csv"), index_col=0)
        self.pheno = pd.read_csv(os.path.join(self.gse_d, "phenotypes.csv"), index_col=1)                    
    
    def add_target_lables(self):
        # Rename targets (labels) of data to 0/1 instead of case/control, non-responders/responders etc.

        # Get samples for which we have a label:
        samps_with_data = self.df.index
        samps_with_label = self.groups.index
        samps_with_both = [s for s in samps_with_data if s in samps_with_label]       
        # print(len(samps_with_label), len(samps_with_both)) # ***Debugging
        orig_labels = pd.Series(index=samps_with_data) # Keep labels in same order as data
        orig_labels.loc[samps_with_both] = self.groups.loc[samps_with_both, "Group"]
        orig_label_names = orig_labels.unique()
        self.orig_label_names = orig_label_names
        if 'case' in orig_label_names and 'control' in orig_label_names:
            self.y = orig_labels.apply(lambda x: 0 if x=="control" else 1) # Binary Series with case/controls as 1/0 
            self.y.loc[orig_labels.isnull()==True] = np.nan # Keep missing target labels as NaN
        else: 
            if 'case' in orig_label_names or 'control' in orig_label_names:
                print("Only one label recognized")
            else:
                print("Unimplemented target labels in dataset!!!")
        label_dist = self.y.value_counts()
        print("Target label counts (0/1): %i / %i"%(label_dist[0], label_dist[1]) )
            

    def organize_data(self):
        # Organize input data for sklearn usage
        self.df = self.mat.transpose() # mat is transposed (rows=CpGs, cols=Pats) -> .df is corrected
        # Shuffle df rows
        self.df = self.df.sample(frac=1, random_state=42)

        
        self.update_features()
        self.update_samples()
        self.add_target_lables() # Add y labels to self.y slot (0/1)

    def update_features(self):
         self.features =  list(self.df.columns)

    def update_samples(self):
        self.samps = list(self.df.index)


# Model training / prediction
# ===========================

def train_on_entire_dataset(Dataset,
                            penalty = 'l1',
                            internalCV_folds = 5,
                            feat_selection="wilcox",
                            feat_selection_pval_thresh=0.01,
                            nan_policy="impute_by_median",
                            out_f=None):
    """ Train model on entire dataset (for testing on a different dataset). Saves model, parameteres used for preprocessing and training."""
    
    start = timeit.default_timer()

    # Remove features that are missing in all samples
    print("Removing/imputing NaN feature values")
    df = Dataset.df.dropna(how="all", axis=1)  ###TODO- remove features above certain fraction of NaNs in train data
    y = Dataset.y
    
    # NaN imputations
    if nan_policy is not None:
        imp_vals = feature_imputation_values(df, nan_policy=nan_policy)
        df = pd.DataFrame(data=numba_fillna(df.values, imp_vals.values), index=df.index, columns=df.columns) # Fast fillna() with imputed values       
    else:
        imp_vals=None
    
    # Feature selection   
    if feat_selection is not None:
        feats_used = select_features(df, y, by=feat_selection, pval_thresh=feat_selection_pval_thresh)
    else:
        feats_used = list(df.columns)
        
    print("Retained %i features"%len(feats_used))
    X = df.loc[:,feats_used].values
        
    model, scaler = train_model(X, y, penalty=penalty, internalCV_folds=internalCV_folds)

    timestamp = datetime.now().strftime("%m/%d/%Y, %H:%M:%S") # Save time model was trained 
    run_params = {"penalty":penalty, "internalCV_folds":internalCV_folds, "feat_selection":feat_selection, "nan_policy":nan_policy, "timestamp":timestamp} # save in output for reference
    outputs = {"trained_model":model, "features_used":feats_used, "imputation_vals":imp_vals, "scaler":scaler, "run_params":run_params}
    
    stop = timeit.default_timer()
    print('Run time: %.1f sec'%(stop - start))

    if out_f is not None:
        save_outputs(outputs, out_f)
        
    return (outputs)


def select_features(df, y, by="wilcox", pval_thresh=0.05):
    if by=="wilcox":
        print('Selecting features using wilcoxon')
        p_vals = stats.mannwhitneyu(df.iloc[np.where(y==0)[0], :], df.iloc[np.where(y==1)[0], :])[1]
        feats_to_keep = list(compress(df.columns, np.where(p_vals<pval_thresh)[0]))
    elif by=="ttest":
        print('Selecting features using wilcoxon')
        p_vals = stats.ttest_ind(df.iloc[np.where(y==0)[0], :], df.iloc[np.where(y==1)[0], :])[1]
        feats_to_keep = list(compress(df.columns, np.where(p_vals<pval_thresh)[0]))
    else:
        print("no feature selection applied")
        feats_to_keep = list(df.columns)
    return(feats_to_keep)
    

def cv_train_test(Dataset, 
                  CV = 5, # "LOO" # 10 #"LOO"
                  penalty = 'l1', 
                  internalCV_folds = 5,
                  feat_selection="wilcox",
                  feat_selection_pval_thresh=0.01,
                  nan_policy="impute_by_median",
                  out_f=None):
    """ Perform CV training and evaluation on DNAm Dataset object """
    
    cv = LeaveOneOut() if CV=="LOO" else KFold(n_splits=CV)
    X_tests, y_tests, models, scalers, feats_used, y_preds, pred_probs = [], [], [], [], [], [], []

    print ("Starting cross validation")
    for fold, (train_index, test_index) in enumerate(cv.split(Dataset.df)):
        
        print("Starting fold", fold, "- Train-test splitting")
        start = timeit.default_timer()
        # split df to train test (instead of np.array) for wilcoxon comparions (can be optimized for speed later)
        df_train, df_test = Dataset.df.iloc[train_index], Dataset.df.iloc[test_index] 
        y_train, y_test = Dataset.y.iloc[train_index], Dataset.y.iloc[test_index]
        
        # print("=========")
        # print(train_index[:5], test_index[:5])
        # print(df_train.iloc[:5,:5], "\n", y_train.iloc[:5], "\n---\n", df_test.iloc[:5,:5], y_test.iloc[:5])
        # print("=========")
        
        # print(X_train.shape, y_train.shape, X_test.shape, y_test.shape)
        print("CV fold", fold, "Train size: %i, test size: %i (fract positives in train: %.3f)"%(df_train.shape[0], df_test.shape[0], y_train.mean()))
        
        # remove features that are NaN in entire train set:  ###TODO- remove features above certain fraction of NaNs in train data
        df_train = df_train.dropna(how="any", axis=1)
        df_test = df_test.loc[:, df_train.columns]

        if nan_policy is not None: # Impute missing values in train set
            stop1 = timeit.default_timer()
            print('Imputing missing values, elapsed time: %.1f sec'%(stop1 - start))
            imp_vals = feature_imputation_values(df_train, nan_policy=nan_policy)
            # Fill in missing values in train and in test sets by train set imputation values
            df_train = pd.DataFrame(data=numba_fillna(df_train.values, imp_vals.values), index=df_train.index, columns=df_train.columns)
            df_test = pd.DataFrame(data=numba_fillna(df_test.values, imp_vals.values), index=df_test.index, columns=df_test.columns)
            
        stop2 = timeit.default_timer()
        print('Starting feature selection, elapsed time: %.1f sec'%(stop2 - start))
        
        # Feature selection (based on train set contrasting)
        if feat_selection is not None:
            feats_to_keep = select_features(df_train, y_train, by=feat_selection, pval_thresh=feat_selection_pval_thresh)
        else:
            feats_to_keep = list(df_train.columns)
            
        print("Retained %i features"%len(feats_to_keep))
        X_train = df_train.loc[:,feats_to_keep].values
        X_test = df_test.loc[:, feats_to_keep].values    
        
        feats_used.append(feats_to_keep)
        X_tests.append(X_test)
        y_tests.append(y_test)
        
        stop3 = timeit.default_timer()
        print('Feature selection complete, ready for training, elapsed time: %.1f sec'%(stop3 - start))
        
        model, scaler, y_pred, y_pred_prob = train_test(X_train, X_test, y_train, penalty=penalty, internalCV_folds=internalCV_folds)
        models.append(model)
        scalers.append(scaler)
        y_preds.append(list(y_pred))
        pred_probs.append(list(y_pred_prob))
        # pred_probs.append(model.predict_proba(X_test)[:,1])

        stop4 = timeit.default_timer()
        print('Fold complete, fold time: %.1f sec'%(stop4 - start))

    # flatten prediction results from all folds into list
    y_pred  = np.array([item for sublist in y_preds for item in sublist]) # predictions for entire dataset (aggregated across CV folds )
    # preds_prob = np.array([item for sublist in pred_probs for item in sublist] )

    timestamp = datetime.now().strftime("%m/%d/%Y, %H:%M:%S") # Save time models finished training
    run_params = {"penalty":penalty, "CV":CV, "internalCV_folds":internalCV_folds, "feat_selection":feat_selection, "nan_policy":nan_policy, "timestamp":timestamp} # save in output for reference
    outputs = {"trained_models":models, "scalers":scalers, "features_used":feats_used, "X_test_data":X_tests, "y_test":y_tests, "y_pred":y_pred, "y_pred_prob":pred_probs, "run_params":run_params} 
    print("----CV complete----")
    return (outputs)


def model_definition(penalty, seed=42, internalCV_folds=5, n_jobs=-1, Cs=None, l1_ratios=None, max_iter=1000, class_weight='balanced'):  #TODO -FIX DEFAULTS BELOW *********************************
    """ create Logistic regression model object"""
    if Cs is None:
        l1_Cs=np.logspace(-4, 6, num=10) # default values of C to try out in CV (lower=stronger regularization)
        l2_Cs=10
    else:
        l1_Cs=Cs
        l2_Cs=Cs
        
    if l1_ratios is None: # default values of l1 ratios to try out in CV for Elastic Net
        l1_ratios=[0.1,0.5,0.9]
        
    if  penalty is None:
        print("Not applying regularization")
        model = LogisticRegressionCV(cv=internalCV_folds, random_state=seed, penalty='l2', Cs=[100000.0], max_iter=max_iter, class_weight=class_weight) #TODO - CHANGE TO NO REGULAROZATION AT ALL!!!!*********************************
    elif penalty=='l1':
        model = LogisticRegressionCV(cv=internalCV_folds, random_state=seed, penalty='l1', solver='liblinear', Cs=l1_Cs, n_jobs=n_jobs, max_iter=max_iter, class_weight=class_weight)
    elif penalty=='l2':
        model = LogisticRegressionCV(cv=internalCV_folds, random_state=seed, penalty='l2', Cs=l2_Cs, max_iter=max_iter, class_weight=class_weight)
    elif penalty=='elasticnet':
        model = LogisticRegressionCV(cv=internalCV_folds, random_state=seed, penalty='elasticnet', solver='saga', l1_ratios=l1_ratios, class_weight=class_weight, max_iter=max_iter) # Cs=l2_Cs,#TODO - FIX DEFAULT Cs*****************  
    else:
        print("unrecognized penalty")
    # print(model)
    return(model)


def scale_train_data(X_train):
    # Standardizing the features (generally by train set only). 
    # Returns scaled train set data, and the scaler oobject itself for scaling test data later
    scaler = StandardScaler()
    scaler.fit(X_train)
    X_train_scaled = scaler.transform(X_train)    
    return(X_train_scaled, scaler)


def train_test(X_train, X_test, y_train, penalty = 'l1', seed=42, internalCV_folds=5, n_jobs=-1):
    """ Train model and get predictions """
    # Standardizing the features (by train set only)  
    X_train, scaler = scale_train_data(X_train)
    X_test = scaler.transform(X_test)

    # fit model on train, predict on test
    model = model_definition(penalty=penalty, seed=seed, internalCV_folds=internalCV_folds, n_jobs=n_jobs, Cs=None, l1_ratios=None)    
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)
    y_pred_prob = model.predict_proba(X_test)[:,1]
    return(model, scaler, y_pred, y_pred_prob)


def train_model(X, y, penalty='l1', seed=42, internalCV_folds=5, n_jobs=-1):
    """ Train model (no CV or prediction) """

    print("Standizing data")
    # Standardize the features (entire data)
    # scaler = StandardScaler().fit(X)
    # X = scaler.transform(X)
    X, scaler = scale_train_data(X)

    print("Training model")
    # fit model 
    model = model_definition(penalty=penalty, seed=seed, internalCV_folds=internalCV_folds, n_jobs=n_jobs, Cs=None, l1_ratios=None)     
    model.fit(X, y)
    # y_pred = model.predict(X) # prediction on same data used for training
    return(model, scaler)


def save_outputs(outputs, out_f):
    pickle.dump(outputs, open(out_f, 'wb'))
    print ("Outputs saved to", out_f)
    

def feature_imputation_values(df, nan_policy="impute_by_mean"):
    # Impute missing values in train set - return imputed value by mean/median etc. for each feature (returns a pd.Series)
    if nan_policy=="impute_by_mean":
        imp_vals = df.mean()
    elif nan_policy=="impute_by_median":
        imp_vals = df.median()
    elif nan_policy=="zeros":
        imp_vals = pd.Series(data=np.zeros(df.shape[1]), index=df.columns)
    else:
        print("Unimplemented/unrecognized nan_policy defined")
    return(imp_vals)
            


def numba_fillna(array, values):
    """ Speed-optimized df.fillna() function (solution taken from https://www.kaggle.com/code/gogo827jz/optimise-speed-of-filling-nan-function)"""
    if np.isnan(array.sum()):
        array = np.where(np.isnan(array), values, array)
    return array


# Data/Result vizualization tools:
# ================================
def plot_cv_roc(cv_res, plot_individual_folds=True, title_pfx="", out_f=None):
    fprs, tprs, aucs = [], [], []
    base_fpr = np.linspace(0, 1, 101)
    plt.figure()
    for i in range(len(cv_res['trained_models'])):
        try:
            logit_roc_auc = roc_auc_score(cv_res["y_test"][i], cv_res["y_pred_prob"][i])
            aucs.append(logit_roc_auc)
            fpr, tpr, thresholds = roc_curve(cv_res["y_test"][i], cv_res["y_pred_prob"][i])
            if plot_individual_folds:
                plt.plot(fpr, tpr, 'b', alpha=0.15)
            tpr = np.interp(base_fpr, fpr, tpr)
            tpr[0] = 0.0
            tprs.append(tpr)
        except:
            print("Some CV folds could not be plotted (missing labels). Trying plot_cv_single_roc() instead.")
            plt.close()
            plot_cv_single_roc(cv_res, title_pfx=title_pfx, out_f=out_f)
            return 
            # continue
    
    tprs = np.array(tprs)
    mean_tprs = tprs.mean(axis=0)
    std = tprs.std(axis=0)
    tprs_upper = np.minimum(mean_tprs + std, 1)
    tprs_lower = mean_tprs - std
    mean_auc = np.mean(aucs)    

    plt.plot(base_fpr, mean_tprs, 'darkblue', label="AUC=%0.2f"%mean_auc)
    plt.fill_between(base_fpr, tprs_lower, tprs_upper, color='lightgrey', alpha=0.3)    
    plt.plot([0, 1], [0, 1],'k--')
    plt.xlim([-0.01, 1.01])
    plt.ylim([-0.01, 1.01])
    plt.ylabel('True Positive Rate')
    plt.xlabel('False Positive Rate')
    plt.title(title_pfx + ' Receiver operating characteristic')
    plt.legend(loc="lower right")
    if out_f is not None:
        plt.savefig(out_f)
    plt.show()
    
    
def plot_cv_single_roc(cv_res, title_pfx="", out_f=None):
    y_true = [item for sublist in cv_res["y_test"] for item in sublist]
    y_probas = [item for sublist in cv_res["y_pred_prob"] for item in sublist]
    auc = roc_auc_score(y_true, y_probas)
    fpr, tpr, thresholds = roc_curve(y_true,  y_probas)
    plt.plot(fpr,tpr, c='darkblue', label="AUC=%0.2f"%auc)
    plt.plot([0, 1], [0, 1],'k--')
    plt.xlim([-0.01, 1.01])
    plt.ylim([-0.01, 1.01])
    plt.ylabel('True Positive Rate')
    plt.xlabel('False Positive Rate')
    plt.title(title_pfx + ' Receiver operating characteristic')
    plt.legend(loc="lower right")
    if out_f is not None:
        plt.savefig(out_f)
    plt.show()

def plot_pred_prob_by_labels(cv_res, title_pfx="", out_f=None):
    y_true = [item for sublist in cv_res["y_test"] for item in sublist]
    y_probs = [item for sublist in cv_res["y_pred_prob"] for item in sublist]
    y_pred = cv_res["y_pred"]
    sns.stripplot(x=y_true, y=y_probs, size=3, palette=["darkred","lightblue"])
    plt.xlabel("True label")
    plt.ylabel("Model prediction\n(prob. for class 1)")

    score = accuracy_score(y_true, y_pred)
    bal_score = balanced_accuracy_score(y_true, y_pred) # Defined as averaged recall for each class
    
    plt.title(title_pfx + ' Predicted probabilities per class\n(Accuracy: %.2f, Balanced Accuracy: %.2f)'%(score, bal_score))

    if out_f is not None:
        plt.savefig(out_f)        
    plt.show()


def print_report(cv_res, THRESH=0.5):
    y_test = [item for sublist in cv_res["y_test"] for item in sublist] # Flatten
    # y_pred = cv_res["y_pred"] # for defined thesholds
    y_pred = np.array([item for sublist in cv_res["y_pred_prob"] for item in sublist]) # Flatten (for using predicted probablities    
    rep = classification_report(y_test, y_pred > THRESH)
    print(rep)



# Validation helper funcs
# =======================
def permute_columns(x):
    # Permute each column of a matrix independently (instead of just reordering the rows)
    row_ndces = np.random.sample(x.shape).argsort(axis=0)
    col_ndces = np.tile(np.arange(x.shape[1]), (x.shape[0], 1))
    return x[row_ndces, col_ndces]