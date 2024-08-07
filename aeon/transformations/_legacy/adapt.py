"""Implements adaptor for applying Scikit-learn-like transformers to time series."""

__maintainer__ = []
__all__ = ["TabularToSeriesAdaptor"]

import numpy as np
from sklearn.base import clone

from aeon.transformations.base import BaseTransformer


class TabularToSeriesAdaptor(BaseTransformer):
    """
    Adapt scikit-learn transformation interface to time series setting.

    This is useful for applying scikit-learn :term:`tabular` transformations
    to :term:`series <Time series>`, but only works with transformations that
    do not require multiple :term:`instances <instance>` for fitting.

    The adaptor behaves as follows:

    If fit_in_transform = False and X is a series (pd.DataFrame, pd.Series, np.ndarray):
        - ``fit(X)`` fits a clone of ``transformer`` to X (considered as a table)
        - ``transform(X)`` applies transformer.transform to X and returns the result
        - ``inverse_transform(X)`` applies tansformer.inverse_transform to X
    If fit_in_transform = True and X is a series (pd.DataFrame, pd.Series, np.ndarray):
        - ``fit`` is empty
        - ``transform(X)`` applies transformer.fit(X).transform.(X) to X,
        considered as a table, and returns the result
        - ``inverse_transform(X)`` applies tansformer(X).inverse_transform(X) to X

    If fit_in_transform = False, and X is of a panel/hierarchical type:
        - ``fit(X)`` fits a clone of ``transformer`` for each individual series x in X
        - ``transform(X)`` applies transform(x) of the clone belonging to x,
        (where the index of x in transform equals the index of x in fit)
        for each individual series x in X, and returns the result
        - ``inverse_transform(X)`` applies transform(x) of the clone belonging to x,
        (where the index of x in transform equals the index of x in fit)
        for each individual series x in X, and returns the result
        .. warning:: instances indices in transform/inverse_transform
            must be equal to those seen in fit
    If fit_in_transform = True, and X is of a panel/hierarchical type:
        - ``fit`` is empty
        - ``transform(X)`` applies transformer.fit(x).transform(x)
        to all individual series x in X and returns the result
        - ``inverse_transform(X)`` applies transformer.fit(x).inverse_transform(x)
        to all individual series x in X and returns the result

    .. warning:: if fit_in_transform is set to False,
        when applied to Panel or Hierarchical data,
        the resulting transformer will identify individual series in test set
        with series indices in training set, on which instances were fit
        in particular, transform will not work if number of instances
        and indices of instances in transform are different from those in fit

    .. warning:: if fit_in_transform is set to True,
        then each series in the test set will be transformed as batch by fit-predict,
        this may cause information leakage in a forecasting setting
        (but not in a time series classification/regression/clustering setting,
        because in these settings the independent samples are the individual series)

    Parameters
    ----------
    transformer : Estimator
        scikit-learn-like transformer to fit and apply to series.
        This is used as a "blueprint" and not fitted or otherwise mutated.

    Attributes
    ----------
    transformer_ : Estimator
        Transformer that is fitted to data, clone of transformer.
    fit_in_transform : bool, default=False
        Whether transformer_ should be fitted in transform (True), or in fit (False)
        recommended setting in forecasting (single series or hierarchical): False.
        recommended setting in classification, regression, clustering: True.
    """

    _tags = {
        "input_data_type": "Series",
        # what is the abstract type of X: Series, or Panel
        "output_data_type": "Series",
        # what abstract type is returned: Primitives, Series, Panel
        "instancewise": True,  # is this an instance-wise transform?
        "X_inner_type": "np.ndarray",
        "y_inner_type": "None",
        "capability:multivariate": True,
        "transform-returns-same-time-index": True,
        "fit_is_empty": False,
    }

    def __init__(self, transformer, fit_in_transform=False):
        self.transformer = transformer
        self.transformer_ = clone(self.transformer)
        self.fit_in_transform = fit_in_transform

        super().__init__()

        if hasattr(transformer, "inverse_transform"):
            self.set_tags(**{"capability:inverse_transform": True})

        # sklearn transformers that are known to fit in transform do not need fit
        if hasattr(transformer, "_get_tags"):
            trafo_fit_in_transform = transformer._get_tags()["stateless"]
        else:
            trafo_fit_in_transform = False

        self._skip_fit = fit_in_transform or trafo_fit_in_transform

        if self._skip_fit:
            self.set_tags(**{"fit_is_empty": True})

    def _fit(self, X, y=None):
        """Fit transformer to X and y.

        private _fit containing the core logic, called from fit

        Parameters
        ----------
        X : 2D np.ndarray
            Data to fit transform to
        y : ignored argument for interface compatibility
            Additional data, e.g., labels for transformation

        Returns
        -------
        self: a fitted instance of the estimator
        """
        if not self._skip_fit:
            self.transformer_.fit(X)
        return self

    def _transform(self, X, y=None):
        """Transform X and return a transformed version.

        private _transform containing the core logic, called from transform

        Parameters
        ----------
        X : 2D np.ndarray
            Data to be transformed
        y : ignored argument for interface compatibility
            Additional data, e.g., labels for transformation

        Returns
        -------
        Xt : 2D np.ndarray
            transformed version of X
        """
        if self._skip_fit:
            Xt = self.transformer_.fit(X).transform(X)
        else:
            Xt = self.transformer_.transform(X)

        # coerce sensibly to 2D np.ndarray
        if isinstance(Xt, (int, float, str)):
            Xt = np.array([[Xt]])
        if not isinstance(Xt, np.ndarray):
            Xt = np.array(Xt)
        if Xt.ndim == 1:
            Xt = Xt.reshape((len(X), 1))

        return Xt

    def _inverse_transform(self, X, y=None):
        """Inverse transform, inverse operation to transform.

        core logic

        Parameters
        ----------
        X : 2D np.ndarray
            Data to be inverse transformed
        y : ignored argument for interface compatibility
            Additional data, e.g., labels for transformation

        Returns
        -------
        Xt : 2D np.ndarray
            inverse transformed version of X
        """
        if self.fit_in_transform:
            Xt = self.transformer_.fit(X).inverse_transform(X)
        else:
            Xt = self.transformer_.inverse_transform(X)
        return Xt

    @classmethod
    def get_test_params(cls, parameter_set="default"):
        """Return testing parameter settings for the estimator.

        Parameters
        ----------
        parameter_set : str, default="default"
            Name of the set of test parameters to return, for use in tests. If no
            special parameters are defined for a value, will return `"default"` set.

        Returns
        -------
        params : dict or list of dict, default = {}
            Parameters to create testing instances of the class
            Each dict are parameters to construct an "interesting" test instance, i.e.,
            `MyClass(**params)` or `MyClass(**params[i])` creates a valid test instance.
            `create_test_instance` uses the first (or only) dictionary in `params`
        """
        from sklearn.preprocessing import StandardScaler

        params1 = {"transformer": StandardScaler(), "fit_in_transform": False}
        params2 = {"transformer": StandardScaler(), "fit_in_transform": True}

        return [params1, params2]
