"""TDE classifiers.

Dictionary based Ordinal TDE classifiers based on SFA transform. Contains a single
IndividualOrdinalTDE and Ordinal TDE.
"""

__maintainer__ = ["RafaAyGar"]
__all__ = [
    "OrdinalTDE",
    "IndividualOrdinalTDE",
    "histogram_intersection",
]

import math
import os
import time
import warnings
from collections import defaultdict

import numpy as np
from joblib import Parallel, delayed
from numba import types
from numba.typed import Dict
from sklearn import preprocessing
from sklearn.kernel_ridge import KernelRidge
from sklearn.utils import check_random_state

from aeon.classification.base import BaseClassifier
from aeon.classification.dictionary_based._tde import histogram_intersection
from aeon.transformations.collection.dictionary_based import SFA


class OrdinalTDE(BaseClassifier):
    """
    Ordinal Temporal Dictionary Ensemble (O-TDE).

    Implementation of the dictionary based Ordinal Temporal Dictionary
    Ensemble as described in [1]_. This method is an ordinal adaptation
    of the Temporal Dictionary Ensemble (TDE) presented in [2]_.

    Overview: Input "n" series length "m" with "d" dimensions.
    O-TDE performs parameter selection to build the ensemble members
    based on a Gaussian process which is intended to predict Mean
    Absolute Error (MAE) values for specific O-TDE parameters configurations.
    Then, the best performing members are selected and used to build the
    final ensemble.

    fit involves finding "n" histograms.

    predict uses 1 nearest neighbor with the histogram intersection distance
    function.

    Parameters
    ----------
    n_parameter_samples : int, default=250
        Number of parameter combinations to consider for the final ensemble.
    max_ensemble_size : int, default=50
        Maximum number of estimators in the ensemble.
    max_win_len_prop : float, default=1
        Maximum window length as a proportion of series length, must be between 0 and 1.
    min_window : int, default=10
        Minimum window length.
    randomly_selected_params : int, default=50
        Number of parameters randomly selected before the Gaussian process parameter
        selection is used.
    bigrams : bool or None, default=None
        Whether to use bigrams, defaults to true for univariate data and false for
        multivariate data.
    dim_threshold : float, default=0.85
        Dimension accuracy threshold for multivariate data, must be between 0 and 1.
    max_dims : int, default=20
        Max number of dimensions per classifier for multivariate data.
    time_limit_in_minutes : int, default=0
        Time contract to limit build time in minutes, overriding n_parameter_samples.
        Default of 0 means n_parameter_samples is used.
    contract_max_n_parameter_samples : int, default=np.inf
        Max number of parameter combinations to consider when time_limit_in_minutes is
        set.
    typed_dict : bool, default=True
        Use a numba typed Dict to store word counts. May increase memory usage, but will
        be faster for larger datasets. As the Dict cannot be pickled currently, there
        will be some overhead converting it to a python dict with multiple threads and
        pickling.
    train_estimate_method : str, default="loocv"
        Method used to generate train estimates in `fit_predict` and
        `fit_predict_proba`. Options are "loocv" for leave one out cross validation and
        "oob" for out of bag estimates.
    n_jobs : int, default=1
        The number of jobs to run in parallel for both `fit` and `predict`.
        ``-1`` means using all processors.
    random_state : int, RandomState instance or None, default=None
        If `int`, random_state is the seed used by the random number generator;
        If `RandomState` instance, random_state is the random number generator;
        If `None`, the random number generator is the `RandomState` instance used
        by `np.random`.

    Attributes
    ----------
    n_classes_ : int
        The number of classes.
    classes_ : list
        The classes labels.
    n_cases_ : int
        The number of train cases.
    n_channels_ : int
        The number of dimensions per case.
    n_timepoints_ : int
        The length of each series.
    estimators_ : list of shape (n_estimators) of IndividualOrdinalTDE
        The collections of estimators trained in fit.
    n_estimators_ : int
        The final number of classifiers used. Will be <= `max_ensemble_size`.
    weights_ : list of shape (n_estimators) of float
        Weight of each estimator in the ensemble.

    See Also
    --------
    IndividualOrdinalTDE, TDE, WEASEL
        Normal versions of TDE.

    References
    ----------
    ..  [1] Rafael Ayllon-Gavilan, David Guijo-Rubio, Pedro Antonio Gutierrez and
        Cesar Hervas-Martinez.
        "A Dictionary-based approach to Time Series Ordinal Classification",
        IWANN 2023. 17th International Work-Conference on Artificial Neural Networks.
    ..  [2] Matthew Middlehurst, James Large, Gavin Cawley and Anthony Bagnall.
        "The Temporal Dictionary Ensemble (TDE) Classifier for Time Series
        Classification", in proceedings of the European Conference on Machine Learning
        and Principles and Practice of Knowledge Discovery in Databases, 2020.

    Examples
    --------
    >>> from aeon.classification.ordinal_classification import OrdinalTDE
    >>> from aeon.datasets import load_unit_test
    >>> X_train, y_train = load_unit_test(split="train")
    >>> X_test, y_test = load_unit_test(split="test")
    >>> clf = OrdinalTDE(
    ...     n_parameter_samples=10,
    ...     max_ensemble_size=3,
    ...     randomly_selected_params=5,
    ... )
    >>> clf.fit(X_train, y_train)
    OrdinalTDE(...)
    >>> y_pred = clf.predict(X_test)
    """

    _tags = {
        "capability:multivariate": True,
        "capability:train_estimate": True,
        "capability:contractable": True,
        "capability:multithreading": True,
        "algorithm_type": "dictionary",
    }

    def __init__(
        self,
        n_parameter_samples=250,
        max_ensemble_size=50,
        max_win_len_prop=1,
        min_window=10,
        randomly_selected_params=50,
        bigrams=None,
        dim_threshold=0.85,
        max_dims=20,
        time_limit_in_minutes=0.0,
        contract_max_n_parameter_samples=np.inf,
        typed_dict=True,
        train_estimate_method="loocv",
        n_jobs=1,
        random_state=None,
    ):
        self.n_parameter_samples = n_parameter_samples
        self.max_ensemble_size = max_ensemble_size
        self.max_win_len_prop = max_win_len_prop
        self.min_window = min_window
        self.randomly_selected_params = randomly_selected_params
        self.bigrams = bigrams

        # multivariate
        self.dim_threshold = dim_threshold
        self.max_dims = max_dims

        self.time_limit_in_minutes = time_limit_in_minutes
        self.contract_max_n_parameter_samples = contract_max_n_parameter_samples
        self.typed_dict = typed_dict
        self.train_estimate_method = train_estimate_method
        self.random_state = random_state
        self.n_jobs = n_jobs

        self.n_cases_ = 0
        self.n_channels_ = 0
        self.n_timepoints_ = 0
        self.n_estimators_ = 0
        self.estimators_ = []
        self.weights_ = []

        self._word_lengths = [16, 14, 12, 10, 8]
        self._norm_options = [True, False]
        self._levels = [1, 2, 3]
        self._igb_options = [True]  # No "equi-depth" in ordinal version
        self._alphabet_size = 4
        self._weight_sum = 0
        self._prev_parameters_x = []
        self._prev_parameters_y = []
        super().__init__()

    def _fit(self, X, y, keep_train_preds=False):
        """Fit an ensemble on cases (X,y), where y is the target variable.

        Build an ensemble of base TDE classifiers from the training set (X,
        y), through an optimised selection over the para space to make a fixed size
        ensemble of the best.

        Parameters
        ----------
        X : 3D np.ndarray of shape = [n_cases, n_channels, n_timepoints]
            The training data.
        y : array-like, shape = [n_cases]
            The class labels.

        Returns
        -------
        self :
            Reference to self.

        Notes
        -----
        Changes state by creating a fitted model that updates attributes
        ending in "_" and sets is_fitted flag to True.
        """
        if self.n_parameter_samples <= self.randomly_selected_params:
            warnings.warn(
                "TemporalDictionaryEnsemble warning: n_parameter_samples <= "
                "randomly_selected_params, ensemble member parameters will be fully "
                "randomly selected.",
                stacklevel=2,
            )

        self.n_cases_, self.n_channels_, self.n_timepoints_ = X.shape

        self.estimators_ = []
        self.weights_ = []
        self._prev_parameters_x = []
        self._prev_parameters_y = []

        # Window length parameter space dependent on series length
        max_window_searches = self.n_timepoints_ / 4
        max_window = int(self.n_timepoints_ * self.max_win_len_prop)

        if self.min_window >= max_window:
            self._min_window = max_window
            warnings.warn(
                f"TemporalDictionaryEnsemble warning: min_window = "
                f"{self.min_window} is larger than max_window = {max_window}."
                f" min_window has been set to {max_window}.",
                stacklevel=2,
            )

        win_inc = int((max_window - self.min_window) / max_window_searches)
        if win_inc < 1:
            win_inc = 1

        possible_parameters = self._unique_parameters(max_window, win_inc)
        num_classifiers = 0
        subsample_size = int(self.n_cases_ * 0.7)
        highest_mae = 0
        highest_mae_idx = 0

        time_limit = self.time_limit_in_minutes * 60
        start_time = time.time()
        train_time = 0
        if time_limit > 0:
            n_parameter_samples = 0
            contract_max_n_parameter_samples = self.contract_max_n_parameter_samples
        else:
            n_parameter_samples = self.n_parameter_samples
            contract_max_n_parameter_samples = np.inf

        rng = check_random_state(self.random_state)

        if self.bigrams is None:
            if self.n_channels_ > 1:
                use_bigrams = False
            else:
                use_bigrams = True
        else:
            use_bigrams = self.bigrams

        # use time limit or n_parameter_samples if limit is 0
        while (
            (
                train_time < time_limit
                and num_classifiers < contract_max_n_parameter_samples
            )
            or num_classifiers < n_parameter_samples
        ) and len(possible_parameters) > 0:
            if num_classifiers < self.randomly_selected_params:
                parameters = possible_parameters.pop(
                    rng.randint(0, len(possible_parameters))
                )
            else:
                scaler = preprocessing.StandardScaler()
                scaler.fit(self._prev_parameters_x)
                gp = KernelRidge(kernel="poly", degree=1)
                gp.fit(
                    scaler.transform(self._prev_parameters_x), self._prev_parameters_y
                )
                preds = gp.predict(scaler.transform(possible_parameters))
                parameters = possible_parameters.pop(
                    rng.choice(np.flatnonzero(preds == preds.min()))
                )

            subsample = rng.choice(self.n_cases_, size=subsample_size, replace=False)
            X_subsample = X[subsample]
            y_subsample = y[subsample]

            tde = IndividualOrdinalTDE(
                *parameters,
                alphabet_size=self._alphabet_size,
                bigrams=use_bigrams,
                dim_threshold=self.dim_threshold,
                max_dims=self.max_dims,
                typed_dict=self.typed_dict,
                n_jobs=self._n_jobs,
                random_state=self.random_state,
            )
            tde.fit(X_subsample, y_subsample)
            tde._subsample = subsample

            tde._mae = self._individual_train_mae(
                tde,
                y_subsample,
                subsample_size,
                100 if num_classifiers < self.max_ensemble_size else highest_mae,
                keep_train_preds,
            )

            w = 1 / (1 + abs(tde._mae))
            if w >= 0:
                weight = math.pow(w, 4)
            else:
                weight = 0.000000001

            if num_classifiers < self.max_ensemble_size:
                if tde._mae > highest_mae:
                    highest_mae = tde._mae
                    highest_mae_idx = num_classifiers
                self.weights_.append(weight)
                self.estimators_.append(tde)
            else:
                if tde._mae < highest_mae:
                    self.weights_[highest_mae_idx] = weight
                    self.estimators_[highest_mae_idx] = tde
                    highest_mae, highest_mae_idx = self._worst_ensemble_mae()

            self._prev_parameters_x.append(parameters)
            self._prev_parameters_y.append(tde._mae)

            num_classifiers += 1
            train_time = time.time() - start_time

        self.n_estimators_ = len(self.estimators_)
        self._weight_sum = np.sum(self.weights_)

        return self

    def _predict(self, X) -> np.ndarray:
        """Predict class values of n instances in X.

        Parameters
        ----------
        X : 3D np.ndarray of shape = [n_cases, n_channels, n_timepoints]
            The data to make predictions for.

        Returns
        -------
        y : array-like, shape = [n_cases]
            Predicted class labels.
        """
        rng = check_random_state(self.random_state)
        return np.array(
            [
                self.classes_[int(rng.choice(np.flatnonzero(prob == prob.max())))]
                for prob in self._predict_proba(X)
            ]
        )

    def _predict_proba(self, X) -> np.ndarray:
        """Predict class probabilities for n instances in X.

        Parameters
        ----------
        X : 3D np.ndarray of shape = [n_cases, n_channels, n_timepoints]
            The data to make predict probabilities for.

        Returns
        -------
        y : array-like, shape = [n_cases, n_classes_]
            Predicted probabilities using the ordering in classes_.
        """
        sums = np.zeros((X.shape[0], self.n_classes_))

        for n, clf in enumerate(self.estimators_):
            preds = clf.predict(X)
            for i in range(0, X.shape[0]):
                sums[i, self._class_dictionary[preds[i]]] += self.weights_[n]

        return sums / (np.ones(self.n_classes_) * self._weight_sum)

    def _fit_predict(self, X, y) -> np.ndarray:
        rng = check_random_state(self.random_state)
        return np.array(
            [
                self.classes_[int(rng.choice(np.flatnonzero(prob == prob.max())))]
                for prob in self._fit_predict_proba(X, y)
            ]
        )

    def _fit_predict_proba(self, X, y) -> np.ndarray:
        self._fit(X, y, keep_train_preds=True)

        results = np.zeros((self.n_cases_, self.n_classes_))
        divisors = np.zeros(self.n_cases_)

        if self.train_estimate_method.lower() == "loocv":
            for i, clf in enumerate(self.estimators_):
                subsample = clf._subsample
                preds = clf._train_predictions

                for n, pred in enumerate(preds):
                    results[subsample[n]][
                        self._class_dictionary[pred]
                    ] += self.weights_[i]
                    divisors[subsample[n]] += self.weights_[i]
        elif self.train_estimate_method.lower() == "oob":
            indices = range(self.n_cases_)
            for i, clf in enumerate(self.estimators_):
                oob = [n for n in indices if n not in clf._subsample]

                if len(oob) == 0:
                    continue

                preds = clf.predict(X[oob])

                for n, pred in enumerate(preds):
                    results[oob[n]][self._class_dictionary[pred]] += self.weights_[i]
                    divisors[oob[n]] += self.weights_[i]
        else:
            raise ValueError(
                "Invalid train_estimate_method. Available options: loocv, oob"
            )

        for i in range(self.n_cases_):
            results[i] = (
                np.ones(self.n_classes_) * (1 / self.n_classes_)
                if divisors[i] == 0
                else results[i] / (np.ones(self.n_classes_) * divisors[i])
            )

        return results

    def _worst_ensemble_mae(self):
        worst_mae = 0.0
        worst_mae_idx = 0

        for c, classifier in enumerate(self.estimators_):
            if classifier._mae > worst_mae:
                worst_mae = classifier._mae
                worst_mae_idx = c

        return worst_mae, worst_mae_idx

    def _unique_parameters(self, max_window, win_inc):
        possible_parameters = [
            [win_size, word_len, normalise, levels, igb]
            for normalise in self._norm_options
            for win_size in range(self.min_window, max_window + 1, win_inc)
            for word_len in self._word_lengths
            for levels in self._levels
            for igb in self._igb_options
        ]

        return possible_parameters

    def _individual_train_mae(self, tde, y, train_size, highest_mae, keep_train_preds):
        absolute_error = 0

        if self._n_jobs > 1:
            c = Parallel(n_jobs=self._n_jobs)(
                delayed(tde._train_predict)(
                    i,
                )
                for i in range(train_size)
            )

            for i in range(train_size):
                absolute_error += abs(int(y[i]) - int(c[i]))

                if keep_train_preds:
                    tde._train_predictions.append(c[i])
        else:
            for i in range(train_size):
                c = tde._train_predict(i)

                absolute_error += abs(int(y[i]) - int(c))

                if keep_train_preds:
                    tde._train_predictions.append(c)

        mae = absolute_error / train_size
        if mae > highest_mae:
            return 100
        return mae

    @classmethod
    def get_test_params(cls, parameter_set="default"):
        """Return testing parameter settings for the estimator.

        Parameters
        ----------
        parameter_set : str, default="default"
            Name of the set of test parameters to return, for use in tests. If no
            special parameters are defined for a value, will return `"default"` set.
            For classifiers, a "default" set of parameters should be provided for
            general testing, and a "results_comparison" set for comparing against
            previously recorded results if the general set does not produce suitable
            probabilities to compare against.

        Returns
        -------
        params : dict or list of dict, default={}
            Parameters to create testing instances of the class.
            Each dict are parameters to construct an "interesting" test instance, i.e.,
            `MyClass(**params)` or `MyClass(**params[i])` creates a valid test instance.
            `create_test_instance` uses the first (or only) dictionary in `params`.
        """
        if parameter_set == "results_comparison":
            return {
                "n_parameter_samples": 10,
                "max_ensemble_size": 5,
                "randomly_selected_params": 5,
            }
        elif parameter_set == "contracting":
            return {
                "time_limit_in_minutes": 5,
                "contract_max_n_parameter_samples": 5,
                "max_ensemble_size": 2,
                "randomly_selected_params": 3,
            }
        else:
            return {
                "n_parameter_samples": 5,
                "max_ensemble_size": 2,
                "randomly_selected_params": 3,
            }


class IndividualOrdinalTDE(BaseClassifier):
    """Single O-TDE classifier.

    An ordinal version of the IndividualTDE described in [2]_.
    Base classifier for the O-TDE classifier. Implementation of single O-TDE base model
    from [1]_.

    Overview: input "n" series of length "m" and IndividualOrdinalTDE performs a SFA
    transform to form a sparse dictionary of discretised words. The binning thresholds
    are obtained from a DecisionTreeRegressor which considers as splitting criterion
    the friedman mse metric. Then, histograms are formed from the discretised words for
    each time series.

    fit involves finding "n" histograms.

    predict uses 1 nearest neighbor with the histogram intersection distance function.

    Parameters
    ----------
    window_size : int, default=10
        Size of the window to use in the SFA transform.
    word_length : int, default=8
        Length of word to use to use in the SFA transform.
    norm : bool, default=False
        Whether to normalize SFA words by dropping the first Fourier coefficient.
    levels : int, default=1
        The number of spatial pyramid levels for the SFA transform.
    igb : bool, default=False
        Whether to use Information Gain Binning (IGB) or
        Multiple Coefficient Binning (MCB) for the SFA transform.
    alphabet_size : default=4
        Number of possible letters (values) for each word.
    bigrams : bool, default=False
        Whether to record word bigrams in the SFA transform.
    dim_threshold : float, default=0.85
        Accuracy threshold as a propotion of the highest accuracy dimension for words
        extracted from each dimensions. Only applicable for multivariate data.
    max_dims : int, default=20
        Maximum number of dimensions words are extracted from. Only applicable for
        multivariate data.
    typed_dict : bool, default=True
        Use a numba TypedDict to store word counts. May increase memory usage, but will
        be faster for larger datasets.
    n_jobs : int, default=1
        The number of jobs to run in parallel for both `fit` and `predict`.
        ``-1`` means using all processors.
    random_state : int or None, default=None
        Seed for random, integer.

    Attributes
    ----------
    n_classes_ : int
        The number of classes.
    classes_ : list
        The classes labels.
    n_cases_ : int
        The number of train cases.
    n_channels_ : int
        The number of dimensions per case.
    n_timepoints_ : int
        The length of each series.

    See Also
    --------
    TemporalDictinaryEnsemble, SFA

    Notes
    -----
    For the Java version, see
    `TSML <https://github.com/uea-machine-learning/tsml/blob/master/src/main/java/
    tsml/classifiers/dictionary_based/IndividualOrdinalTDE.java>`_.

    References
    ----------
    .. [1] Rafael Ayllon-Gavilan, David Guijo-Rubio, Pedro Antonio Gutierrez and
        Cesar Hervas-Martinez.
        "A Dictionary-based approach to Time Series Ordinal Classification",
        IWANN 2023. 17th International Work-Conference on Artificial Neural Networks.
    .. [2] Matthew Middlehurst, James Large, Gavin Cawley and Anthony Bagnall
        "The Temporal Dictionary Ensemble (TDE) Classifier for Time Series
        Classification", in proceedings of the European Conference on Machine Learning
        and Principles and Practice of Knowledge Discovery in Databases, 2020.

    Examples
    --------
    >>> from aeon.classification.ordinal_classification import IndividualOrdinalTDE
    >>> from aeon.datasets import load_unit_test
    >>> X_train, y_train = load_unit_test(split="train")
    >>> X_test, y_test = load_unit_test(split="test")
    >>> clf = IndividualOrdinalTDE()
    >>> clf.fit(X_train, y_train)
    IndividualOrdinalTDE(...)
    >>> y_pred = clf.predict(X_test)
    """

    _tags = {
        "capability:multivariate": True,
        "capability:multithreading": True,
    }

    def __init__(
        self,
        window_size=10,
        word_length=8,
        norm=False,
        levels=1,
        igb=False,
        alphabet_size=4,
        bigrams=True,
        dim_threshold=0.85,
        max_dims=20,
        typed_dict=True,
        n_jobs=1,
        random_state=None,
    ):
        self.window_size = window_size
        self.word_length = word_length
        self.norm = norm
        self.levels = levels
        self.igb = igb
        self.alphabet_size = alphabet_size
        self.bigrams = bigrams

        # multivariate
        self.dim_threshold = dim_threshold
        self.max_dims = max_dims

        self.typed_dict = typed_dict
        self.n_jobs = n_jobs
        self.random_state = random_state

        self.n_cases_ = 0
        self.n_channels_ = 0
        self.n_timepoints_ = 0

        # we will disable typed_dict if numba is disabled
        self._typed_dict = typed_dict and not os.environ.get("NUMBA_DISABLE_JIT") == "1"

        self._transformers = []
        self._transformed_data = []
        self._class_vals = []
        self._dims = []
        self._highest_dim_bit = 0
        self._accuracy = 0
        self._subsample = []
        self._train_predictions = []

        super().__init__()

    # todo remove along with BOSS and SFA workarounds when Dict becomes serialisable.
    def __getstate__(self):
        """Return state as dictionary for pickling, required for typed Dict objects."""
        state = self.__dict__.copy()
        if self._typed_dict:
            nl = [None] * len(self._transformed_data)
            for i, ndict in enumerate(state["_transformed_data"]):
                pdict = dict()
                for key, val in ndict.items():
                    pdict[key] = val
                nl[i] = pdict
            state["_transformed_data"] = nl
        return state

    def __setstate__(self, state):
        """Set current state using input pickling, required for typed Dict objects."""
        self.__dict__.update(state)
        if self._typed_dict:
            nl = [None] * len(self._transformed_data)
            for i, pdict in enumerate(self._transformed_data):
                ndict = (
                    Dict.empty(
                        key_type=types.UniTuple(types.int64, 2), value_type=types.uint32
                    )
                    if self.levels > 1 or self.n_channels_ > 1
                    else Dict.empty(key_type=types.int64, value_type=types.uint32)
                )
                for key, val in pdict.items():
                    ndict[key] = val
                nl[i] = ndict
            self._transformed_data = nl

    def _fit(self, X, y):
        """Fit a single base TDE classifier on n_cases cases (X,y).

        Parameters
        ----------
        X : 3D np.ndarray of shape = [n_cases, n_channels, n_timepoints]
            The training data.
        y : array-like, shape = [n_cases]
            The class labels.

        Returns
        -------
        self :
            Reference to self.

        Notes
        -----
        Changes state by creating a fitted model that updates attributes
        ending in "_" and sets is_fitted flag to True.
        """
        self.n_cases_, self.n_channels_, self.n_timepoints_ = X.shape
        self._class_vals = y

        # select dimensions using accuracy estimate if multivariate
        if self.n_channels_ > 1:
            self._dims, self._transformers = self._select_dims(X, y)

            words = (
                [
                    Dict.empty(
                        key_type=types.UniTuple(types.int64, 2), value_type=types.uint32
                    )
                    for _ in range(self.n_cases_)
                ]
                if self._typed_dict
                else [defaultdict(int) for _ in range(self.n_cases_)]
            )

            for i, dim in enumerate(self._dims):
                X_dim = X[:, dim, :].reshape(self.n_cases_, 1, self.n_timepoints_)
                dim_words = self._transformers[i].transform(X_dim, y)
                dim_words = dim_words[0]

                for n in range(self.n_cases_):
                    if self._typed_dict:
                        for word, count in dim_words[n].items():
                            if self.levels > 1:
                                words[n][
                                    (word[0], word[1] << self._highest_dim_bit | dim)
                                ] = count
                            else:
                                words[n][(word, dim)] = count
                    else:
                        for word, count in dim_words[n].items():
                            words[n][word << self._highest_dim_bit | dim] = count

            self._transformed_data = words
        else:
            self._transformers.append(
                SFA(
                    word_length=self.word_length,
                    alphabet_size=self.alphabet_size,
                    window_size=self.window_size,
                    norm=self.norm,
                    levels=self.levels,
                    binning_method="information-gain-mae" if self.igb else "equi-depth",
                    bigrams=self.bigrams,
                    remove_repeat_words=True,
                    lower_bounding=False,
                    save_words=False,
                    use_fallback_dft=True,
                    typed_dict=self.typed_dict,
                    n_jobs=self._n_jobs,
                    random_state=self.random_state,
                )
            )
            self._transformers[0].fit(X, y)
            sfa = self._transformers[0].transform(X, y)
            self._transformed_data = sfa[0]

    def _predict(self, X):
        """Predict class values of all instances in X.

        Parameters
        ----------
        X : 3D np.ndarray of shape = [n_cases, n_channels, n_timepoints]
            The data to make predictions for.

        Returns
        -------
        y : array-like, shape = [n_cases]
            Predicted class labels.
        """
        n_cases = X.shape[0]

        if self.n_channels_ > 1:
            words = (
                [
                    Dict.empty(
                        key_type=types.UniTuple(types.int64, 2), value_type=types.uint32
                    )
                    for _ in range(n_cases)
                ]
                if self._typed_dict
                else [defaultdict(int) for _ in range(n_cases)]
            )

            for i, dim in enumerate(self._dims):
                X_dim = X[:, dim, :].reshape(n_cases, 1, self.n_timepoints_)
                dim_words = self._transformers[i].transform(X_dim)
                dim_words = dim_words[0]

                for n in range(n_cases):
                    if self._typed_dict:
                        for word, count in dim_words[n].items():
                            if self.levels > 1:
                                words[n][
                                    (word[0], word[1] << self._highest_dim_bit | dim)
                                ] = count
                            else:
                                words[n][(word, dim)] = count
                    else:
                        for word, count in dim_words[n].items():
                            words[n][word << self._highest_dim_bit | dim] = count

            test_bags = words
        else:
            test_bags = self._transformers[0].transform(X)
            test_bags = test_bags[0]

        classes = Parallel(n_jobs=self._n_jobs, prefer="threads")(
            delayed(self._test_nn)(
                test_bag,
            )
            for test_bag in test_bags
        )

        return np.array(classes)

    def _test_nn(self, test_bag):
        rng = check_random_state(self.random_state)

        best_sim = -1
        nn = None

        for n, bag in enumerate(self._transformed_data):
            sim = histogram_intersection(test_bag, bag)

            if sim > best_sim or (sim == best_sim and rng.random() < 0.5):
                best_sim = sim
                nn = self._class_vals[n]

        return nn

    def _select_dims(self, X, y):
        self._highest_dim_bit = (math.ceil(math.log2(self.n_channels_))) + 1
        maes = []
        transformers = []

        # select dimensions based on reduced bag size accuracy
        for i in range(self.n_channels_):
            self._dims.append(i)
            transformers.append(
                SFA(
                    word_length=self.word_length,
                    alphabet_size=self.alphabet_size,
                    window_size=self.window_size,
                    norm=self.norm,
                    levels=self.levels,
                    binning_method="information-gain-mae" if self.igb else "equi-depth",
                    bigrams=self.bigrams,
                    remove_repeat_words=True,
                    lower_bounding=False,
                    save_words=False,
                    keep_binning_dft=True,
                    use_fallback_dft=True,
                    typed_dict=self.typed_dict,
                    n_jobs=self._n_jobs,
                )
            )

            X_dim = X[:, i, :].reshape(self.n_cases_, 1, self.n_timepoints_)

            transformers[i].fit(X_dim, y)
            sfa = transformers[i].transform(
                X_dim,
                y,
            )
            transformers[i].keep_binning_dft = False
            transformers[i].binning_dft = None

            total_absolute_err = 0
            for i in range(self.n_cases_):
                absolute_err = abs(int(y[i]) - int(self._train_predict(i, sfa[0])))
                total_absolute_err += absolute_err
            mae = total_absolute_err / self.n_cases_
            maes.append(mae)

        min_mae = min(maes)

        dims = []
        fin_transformers = []
        mae_min_threshold = 1 + (1 - self.dim_threshold)
        for i in range(self.n_channels_):
            if maes[i] <= min_mae * mae_min_threshold:
                dims.append(i)
                fin_transformers.append(transformers[i])

        if len(dims) > self.max_dims:
            rng = check_random_state(self.random_state)
            idx = rng.choice(len(dims), self.max_dims, replace=False).tolist()
            dims = [dims[i] for i in idx]
            fin_transformers = [fin_transformers[i] for i in idx]

        return dims, fin_transformers

    def _train_predict(self, train_num, bags=None):
        if bags is None:
            bags = self._transformed_data

        test_bag = bags[train_num]
        best_sim = -1
        nn = None

        for n, bag in enumerate(bags):
            if n == train_num:
                continue

            sim = histogram_intersection(test_bag, bag)

            if sim > best_sim:
                best_sim = sim
                nn = self._class_vals[n]

        return nn
