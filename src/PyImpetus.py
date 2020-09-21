import numpy as np
from sklearn.base import TransformerMixin, BaseEstimator
from sklearn.model_selection import train_test_split
from sklearn.metrics import log_loss, mean_squared_error
import scipy.stats as ss
import copy
from collections import Counter
from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor
from sklearn.model_selection._split import check_cv
from sklearn.utils.validation import check_is_fitted



class inter_IAMB(TransformerMixin, BaseEstimator):
    """
    PyImpetus - interIAMB feature selection algorithm with the conditional mutual
    information part being replaced by a conditional test.

    PyImpetus is a feature selection algorithm that picks features by considering their performance
    both individually as well as conditioned on other selected features. This allows the algorithm
    to not only select the best set of features, it also selects the best set of features
    that play well with each other. For example, the best performing feature might not
    play well with others while the remaining features, when taken together could out-perform
    the best feature. PyImpetus takes this into account and produces the best possible combination.

    Parameters
    ----------
    model : estimator object, default=None
        The model which will be used for conditional feature selection. If ``None``,
        will use ``DecisionTreeRegressor`` or ``DecisionTreeClassifier`` depending on the
        ``regression`` param.

    min_feat_proba_thresh : float, default=0.1
        The minimum probability of occurrence that a feature should possess over all
        folds for it to be considered in the final MB.

    p_val_thresh : float, default=0.05
        The p-value below which the feature will considered as a candidate for the final MB.

    k_feats_select : int, default=5
        The number of features to select during growth phase of InterIAMB algorithm.

    num_simul : int, default=100
        Number of train-test splits to perform to check usefulness of each feature.

    cv : int, cross-validation generator or an iterable, default=None
        Determines the cross-validation splitting strategy.
        Possible inputs for cv are:

        - None, to use the default 5-fold cross validation,
        - integer, to specify the number of folds in a (Stratified)KFold,
        - CV splitter,
        - An iterable yielding (train, test) splits as arrays of indices.

        For integer/None inputs, if the ``regression`` param is False and ``y`` is
        either binary or multiclass, StratifiedKFold is used. In all
        other cases, KFold is used.

    regression : bool, default=False
        Defines the task - whether it is regression or classification.

    verbose : int, default=1
        Controls the verbosity: the higher, the more messages.

    Attributes
    ----------
    final_feats_ : ndarray or list of ndarray of shape (n_classes,)
        Final list of features.

    Notes
    -----
    PyImpetus is basically the interIAMB algorithm as provided in the paper
    "An Improved IAMB Algorithm for Markov Blanket Discovery" [1]_ with the conditional mutual information
    part being replaced by a conditional test. This test is as described in the paper
    "Testing Conditional Independence in Supervised Learning Algorithms" [2]_.

    References
    ----------
    .. [1] Zhang, Yishi and Zhang, Zigang and Liu, Kaijun and Qian, Gangyi,
       "An Improved IAMB Algorithm for Markov Blanket Discovery", 
       JCP, 5, 1755-1761, 10.4304/jcp.5.11.1755-1761, 2010.

    .. [2] David S. Watson and Marvin N. Wright, "Testing Conditional
       Independence in Supervised Learning Algorithms", arXiv, 1901.09917, 2019.
    """

    def __init__(self, model=None, min_feat_proba_thresh=0.1, p_val_thresh=0.05, k_feats_select=5, num_simul=100, cv=5, regression=False, verbose=1):
        # Defines the model which will be used for conditional feature selection
        if model:
            self.model = model
        elif regression:
            self.model = DecisionTreeRegressor(random_state=27)
        else:
            self.model = DecisionTreeClassifier(random_state=27)
        # The minimum probability of occurrence that a feature should possess over all folds for it to be considered in the final MB
        self.min_feat_proba_thresh = min_feat_proba_thresh
        # The p-value below which the feature will considered as a candidate for the final MB
        self.p_val_thresh = p_val_thresh
        # The number of features to select during growth phase of InterIAMB algorithm
        self.k_feats_select = k_feats_select
        # Number of train-test splits to perform to check usefulness of each feature
        self.num_simul = num_simul
        # Number of folds for CV 
        self.cv = cv
        # For printing some stuff
        self.verbose = verbose
        # Defines the task whether it is regression or classification
        self.regression = regression


    def _CPI(self, X, Y, Z, B, orig_model, regression):
        # Generate the data matrix
        # Always keep the variable to check, in front
        X = np.reshape(X, (-1, 1))
        if Z is None:
            data = X
        elif Z.ndim == 1:
            data = np.concatenate((X, np.reshape(Z, (-1, 1))), axis=1)
        else:
            data = np.concatenate((X, Z), axis=1)

        # testX -> prediction on correct set
        # testY -> prediction on permuted set
        testX, testY = list(), list()

        for i in range(B):
            x_train, x_test, y_train, y_test = train_test_split(data, Y, test_size=0.2, random_state=i)
            model = copy.deepcopy(orig_model)
            model.fit(x_train, y_train)
            if regression:
                preds = model.predict(x_test)
                testX.append(mean_squared_error(y_test, preds))
            else:
                preds = model.predict_proba(x_test)[:,1]
                testX.append(log_loss(y_test, preds))

            # Perform permutation
            np.random.seed(i)
            np.random.shuffle(x_train[:,0])
            np.random.shuffle(x_test[:,0])

            model = copy.deepcopy(orig_model)
            model.fit(x_train, y_train)
            if regression:
                preds = model.predict(x_test)
                testY.append(mean_squared_error(y_test, preds))
            else:
                preds = model.predict_proba(x_test)[:,1]
                testY.append(log_loss(y_test, preds))

        # Since we want log_loss to be lesser for testX,
        # we therefore perform one tail paired t-test (to the left and not right)
        t_stat, p_val = ss.ttest_ind(testX, testY, nan_policy='omit')
        return ss.t.cdf(t_stat, len(testX) + len(testY) - 2) # Left part of t-test



       
    # Function that performs the growth stage of the Inter-IAMB algorithm
    def _grow(self, data, orig_data, Y, MB, n, B, model, regression):
        best = list()
        # For each feature in MB, check if it is false positive
        for col in data.columns:
            if len(MB) > 0:
                p_val = self._CPI(data[col].values, Y, orig_data[MB].values, B, model, regression)
            else:
                p_val = self._CPI(data[col].values, Y, None, B,  model, regression)
            best.append([col, p_val])
        
        # Sort and pick the top n features
        best.sort(key = lambda x: x[1])
        return best[:n]




    # Function that performs the shrinking stage of the Inter-IAMB algorithm
    def _shrink(self, data, Y, MB, thresh, B, model, regression):
        # Reverse the MB and shrink since the first feature in the list is the most important
        MB.reverse()
        # If there is only one element in the MB, no shrinkage is required
        if len(MB) < 2:
            return list()
        # Generate a list for false positive features
        remove = list()
        # For each feature in MB, check if it is false positive
        for col in MB:
            MB_to_consider = [i for i in MB if i not in [col]+remove]
            # Again, if there is only one element in MB, no shrinkage is required
            if len(MB_to_consider) < 1:
                break
            # Get the p-value from the Conditional Predictive Information Test
            p_val = self._CPI(data[col].values, Y, data[MB_to_consider].values, B, model, regression)
            if p_val > thresh:
                remove.append(col)
        # Reverse back the list
        MB.reverse()
        return remove




    # Function that performs the Inter-IAMB algorithm
    # Outputs the Markov Blanket (MB) as well as the false positive features
    def _inter_IAMB(self, data, Y):
        # Keep a copy of the original data
        orig_data = data.copy()
        Y = np.reshape(Y, (-1, 1))
        MB = list()
        
        # Run the algorithm until there is no change in current epoch MB and previous epoch MB
        while True:
            old_MB = list(MB)
            
            # Growth phase
            best = self._grow(data, orig_data, Y, MB, self.k_feats_select, self.num_simul, self.model, self.regression)
            for best_feat, best_val in best:
                if best_val < self.p_val_thresh:
                    MB.append(best_feat)

            # Shrink phase
            remove_feats = self._shrink(orig_data, Y, MB, self.p_val_thresh, self.num_simul, self.model, self.regression)
            for feat in remove_feats:
                MB.pop(MB.index(feat))
            
            # Remove all features in MB and remove_feats from the dataframe
            feats_to_remove = list()
            for i in MB+remove_feats:
                if i in data.columns:
                    feats_to_remove.append(i)
            data = data.drop(feats_to_remove, axis=1)
            
            # Break if current MB is same as previous MB
            if old_MB == MB:
                break
            print("Candidate features: ", MB)
            
        # Finally, return the Markov Blanket of the target variable
        return MB




    # Call this to get a list of selected features
    def fit(self, X, y, groups=None):
        """
        Fit the feature selector.
        
        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            The data used to compute the per-feature minimum and maximum
            used for later scaling along the features axis.
        
        y : array-like of shape (n_samples, n_output) \
            or (n_samples,), default=None
            Target relative to X for classification or regression.
        
        groups : array-like of shape (n_samples,), default=None
            Group labels for the samples used while splitting the dataset into
            train/test set. Only used in conjunction with a "Group" ``cv``
            instance (e.g., ``~sklearn.model_selection.GroupKFold``).
        
        Returns
        -------
        self
            object
        """
        # The final list of features
        self.final_feats_ = list()

        # List of candidate features from each fold
        feature_sets = list()

        cv = check_cv(self.cv, y, classifier=(not self.regression))
        num_cv_splits = cv.get_n_splits(X, y, groups)

        for cv_index, (train, test) in enumerate(cv.split(X, y, groups)):
            if self.verbose >= 1:
                print("CV Number: ", cv_index+1, "\n#############################")
                
            # Define the X and Y variables
            X_, y_ = X.loc[train], y.values[train]

            # The inter_IAMB function returns a list of features for current fold
            feats = self._inter_IAMB(X_.copy(), y_)
            feature_sets.extend(feats)

            # Do some printing if specified
            if self.verbose >= 1:
                print("\nFinal features selected in this fold: ", feats)
                print()

        # Get the list of all candidate features and their probabilities
        proposed_feats = [[i, j/num_cv_splits] for i, j in Counter(feature_sets).items()]
        # Pretty printing
        if self.verbose >= 1:
            print("\n\nFINAL SELECTED FEATURES\n##################################")
        # Select only those candidate features that have a probability higher than min_feat_proba_thresh
        for a, b in proposed_feats:
            if b > self.min_feat_proba_thresh:
                if self.verbose >= 1:
                    print("Feature: ", a, "\tProbability Score: ", b)
                self.final_feats_.append(a)
        # Finally, return the final set of features
        return self




    # Call this function to simply return the pruned (feature-wise) data
    def transform(self, X, y=None):
        """
        Transform data by selecting features.
        
        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            The data to select features from.
        
        y : Not used.
        
        groups : Not used.
        
        Returns
        -------
        Xt : array-like of shape (n_samples, n_features)
            Transformed data.
        """
        check_is_fitted(self)
        X = X.copy()
        return X[self.final_feats_]




    # A quick wrapper function for fit() and transform()
    def fit_transform(self, X, y, groups=None):
        """
        Fit the feature selector and transform data.
        
        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            The data to select features from.
        
        y : array-like of shape (n_samples, n_output) \
            or (n_samples,), default=None
            Target relative to X for classification or regression.
        
        groups : array-like of shape (n_samples,), default=None
            Group labels for the samples used while splitting the dataset into
            train/test set. Only used in conjunction with a "Group" ``cv``
            instance (e.g., ``~sklearn.model_selection.GroupKFold``).
        
        Returns
        -------
        Xt : array-like of shape (n_samples, n_features)
            Transformed data.
        """
        # First call fit
        self.fit(X, y, groups=groups)
        # Now transform the data
        return self.fit_transform(X)
