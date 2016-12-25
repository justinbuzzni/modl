import numpy as np
import scipy.sparse as sp

from .dict_fact import DictMF
from .dict_fact_fast import _predict


class DictCompleter(DictMF):
    """Matrix factorization estimator based on masked online dictionary
     learning.

    Parameters
    ----------
    alpha: float,
        Regularization of the code (ridge penalty)
    n_components: int,
        Number of components for the dictionary
    learning_rate: float in [0.5, 1],
        Controls the sequence of weights in
         the update of the surrogate function
    batch_size: int,
        Number of samples to consider between each dictionary update
    offset: float,
        Offset in the
    reduction: float,
        Sets how much the data is masked during the algorithm
    fit_intercept: boolean,
        Fixes the first dictionary atom to [1, .., 1]
    dict_init: ndarray (n_components, n_cols),
        Initial dictionary
    l1_ratio: float in [0, 1]:
        Controls the sparsity of the dictionary
    impute: boolean,
        Updates the Gram matrix online (Experimental, non tested)
    max_n_iter: int,
        Number of samples to visit before stopping. If None, fit performs
         a single epoch on data
    random_state: int or RandomState
        Pseudo number generator state used for random sampling.
    verbose: boolean,
        Degree of output the procedure will print.
    backend: str in {'c', 'python'},
        Code base to use: 'c' is faster, but 'python' is easier to hack
    debug: boolean,
        Keep tracks of the surrogate loss during the procedure
    callback: callable,
        Function to be called when printing information
    detrend: boolean,
        Perform matrix decomposition on centered data, and predict data
         accordingly
    crop: 2-uple or None,
        Bounds of matrix values, useful at prediction time


    Attributes
    -------
        self.Q_: ndarray (n_components, n_cols):
            Learned dictionary
    """

    def __init__(self, alpha=1.0, beta=.0,
                 n_components=30, learning_rate=1.,
                 batch_size=1, offset=0,
                 projection='partial',
                 fit_intercept=False, dict_init=None, l1_ratio=0,
                 max_n_iter=0,
                 n_epochs=1,
                 random_state=None, verbose=0, backend='c', debug=False,
                 detrend=False,
                 crop=None,
                 callback=None):
        super(DictCompleter,self).__init__(alpha=alpha,
                         n_components=n_components,
                         # Hyper-parameters
                         learning_rate=learning_rate,
                         batch_size=batch_size,
                         offset=offset,
                         # Reduction parameter
                         reduction=1,
                         projection=projection,
                         fit_intercept=fit_intercept,
                         # Dict parameter
                         dict_init=dict_init,
                         l1_ratio=l1_ratio,
                         # For variance reduction
                         n_samples=None,
                         # Generic parameters
                         max_n_iter=max_n_iter,
                         n_epochs=n_epochs,
                         random_state=random_state,
                         verbose=verbose,
                         backend=backend,
                         debug=debug,
                         callback=callback)
        self.beta = beta
        self.detrend = detrend
        self.crop = crop

    def fit(self, X, y=None):
        """Learns a dictionary from sparse matrix X

        Parameters
        ----------
        X: csr-matrix (n_samples, n_features)
            Datset to learn the dictionary from

        """
        X = sp.csr_matrix(X, dtype='float')

        if self.detrend:
            self.row_mean_, self.col_mean_ = compute_biases(X,
                                                            beta=self.beta,
                                                            inplace=False)
            for i in range(X.shape[0]):
                X.data[X.indptr[i]:X.indptr[i + 1]] -= self.row_mean_[i]
            X.data -= self.col_mean_.take(X.indices, mode='clip')
        DictMF.fit(self, X)

    def predict(self, X):
        """ Predict values of X from internal dictionary and intercepts

        Parameters
        ----------
        X: csr-matrix (n_samples, n_features)
            Matrix holding the loci of prediction

        Returns
        -------
        X_pred: csr-matrix (n_samples, n_features)
            Matrix with the same sparsity structure as X, with predicted values
        """
        X = sp.csr_matrix(X)
        out = np.zeros_like(X.data)
        _predict(out, X.indices, X.indptr, self.code_,
                 self.components_)

        if self.detrend:
            for i in range(X.shape[0]):
                out[X.indptr[i]:X.indptr[i + 1]] += self.row_mean_[i]
            out += self.col_mean_.take(X.indices, mode='clip')

        if self.crop is not None:
            out[out > self.crop[1]] = self.crop[1]
            out[out < self.crop[0]] = self.crop[0]

        return sp.csr_matrix((out, X.indices, X.indptr), shape=X.shape)

    def score(self, X):
        """Score prediction based on root mean squared error"""
        X = sp.csr_matrix(X)
        X_pred = self.predict(X)
        return rmse(X, X_pred)

    def _refit(self, X):
        for i in range(X.shape[0]):
            X.data[X.indptr[i]:X.indptr[i + 1]] -= self.row_mean_[i]
        X.data -= self.col_mean_.take(X.indices, mode='clip')
        DictMF._refit(self, X)


def compute_biases(X, beta=0, inplace=False):
    """Row and column centering from csr matrices

    Parameters
    ----------
    X: csr-matrix (n_samples, n_features)
        Data matrix

    inplace: boolean,
        Perform centering on the input matrix

    Returns
    ---------
    X: csr-matrix (n_samples, n_features)
        Centered data
    """
    if not inplace:
        X = X.copy()
    X = sp.csr_matrix(X)

    acc_u = np.zeros(X.shape[0])
    acc_m = np.zeros(X.shape[1])

    n_u = X.getnnz(axis=1)
    n_m = X.getnnz(axis=0)
    n_u[n_u == 0] = 1
    n_m[n_m == 0] = 1
    print('Centering data')
    average_rating = np.mean(X.data)
    for _ in range(2):
        w_u = (X.sum(axis=1).A[:, 0] + average_rating * beta) / (n_u + beta)
        for i, (left, right) in enumerate(zip(X.indptr[:-1], X.indptr[1:])):
            X.data[left:right] -= w_u[i]
        w_m = X.sum(axis=0).A[0] / (n_m + beta)
        X.data -= w_m.take(X.indices, mode='clip')
        acc_u += w_u
        acc_m += w_m

    return acc_u, acc_m


def _check(X_true, X_pred):
    """Adapted from spira. Input check before scoring"""
    if X_true.shape != X_pred.shape:
        raise ValueError("X_true and X_pred should have the same shape.")

    X_true = sp.csr_matrix(X_true)
    X_pred = sp.csr_matrix(X_pred)

    return X_true, X_pred


def rmse(X_true, X_pred):
    """Root mean squared error for two sparse matrices"""
    X_true, X_pred = _check(X_true, X_pred)
    mse = np.mean((X_true.data - X_pred.data) ** 2)
    return np.sqrt(mse)
