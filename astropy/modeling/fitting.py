# Licensed under a 3-clause BSD style license - see LICENSE.rst
"""
This module provides wrappers, called Fitters, around some Numpy and Scipy
fitting functions. All Fitters take an instance of `~astropy.modeling.core.ParametricModel`
as input and define a ``__call__`` method which fits the model to the data and changes the
model's parameters attribute. The idea is to make this extensible and allow
users to easily add other fitters.

Linear fitting is done using Numpy's `~numpy.linalg.lstsq` function.
There are currently two non-linear fitters which use `~scipy.optimize.leastsq` and
`~scipy.optimize.slsqp` functions in scipy.optimize.

"""
from __future__ import division
import abc
from functools import reduce
import warnings
import numpy as np
from numpy import linalg
from ..logger import log
from .utils import poly_map_domain


__all__ = ['LinearLSQFitter', 'NonLinearLSQFitter', 'SLSQPFitter',
           'JointFitter', 'Fitter']

MAXITER = 100
EPS = np.sqrt(np.finfo(float).eps)

# supported constraints
constraintsdef = {'NonLinearLSQFitter': ['fixed', 'tied', 'bounds'],
                  'SLSQPFitter': ['bounds', 'eqcons', 'ineqcons', 'fixed', 'tied'],
                  'LinearLSQFitter': ['fixed'],
                  }


class ModelsError(Exception):

    """
    Base Error class.
    """
    def __init__(self, message):
        self._message = message

    def __str__(self):
        return self._message


class ModelLinearityError(ModelsError):

    """
    Called when a linear model is passed to a non-linear fitter and vice versa.
    """
    def __init__(self, message):
        super(ModelLinearityError, self).__init__(message)


class UnsupportedConstraintError(ModelsError):

    """
    Raised when a fitter does not support a type of constraint.
    """
    def __init__(self, message):
        super(UnsupportedConstraintError, self).__init__(message)


class Fitter(object):

    """
    Base class for all fitters.

    The purpose of this class is to manage constraints.
    """
    __metaclass__ = abc.ABCMeta

    def __init__(self, model):
        self._model = model
        self._validate_constraints()
        if any(self.model.constraints._fixed.values()) or \
           any(self.model.constraints._tied.values()):
            self._fitpars = self.model.constraints.fitpars[:]
        else:
            self._fitpars = self.model._parameters[:]
        if self._model.deriv is None:
            self.dfunc = None
        else:
            self.dfunc = self._wrap_deriv
        self._weights = None

    @property
    def model(self):
        """
        The model to be fit
        """
        return self._model

    @model.setter
    def model(self, val):
        self._model = val

    @property
    def fitpars(self):
        if any(self.model.constraints._fixed.values()) or \
           any(self.model.constraints._tied.values()):
            return self.model.constraints.fitpars
        else:
            return self.model._parameters

    @fitpars.setter
    def fitpars(self, fps):
        """
        Returns a list of parameters to be passed to the fitting
        algorithm. This is either the model.parameters (if there are
        no constrints or a modified version of model.parameters which takes
        into account constraints.

        Different fitters deal with bounds in a different way. Some set bounds
        internally as part of the algorithm and some don't.
        If a function set_bounds is provided, bounds will be dealt with here.
        This function should be set in the individual fitters.

        Parameters
        ----------
        fps : list
            list of parameters, fitted in a succesive iteration of the
            fitting algorithm
        set_bounds : callable

        """
        if any(self.model.constraints._fixed.values()) or \
           any(self.model.constraints._tied.values()):
            self.model.constraints.fitpars = fps
            self._fitpars[:] = self.model.constraints.fitpars
        elif any([b != (-1E12, 1E12) for b in self.model.constraints.bounds.values()]):
            self._set_bounds(fps)
        else:
            self._fitpars[:] = fps
            self.model.parameters = fps

    def _set_bounds(self, pars):
        """
        This method is to be implemented by subcclasses of Fitter if necessary.

        Diferent fitting algorithms deal with bounds in a different way.
        For example, the SLSQP algorithm accepts bounds as input while
        the leastsq algorithm does not handle bounds at all and they are
        dealt with in a separate method.

        """
        raise NotImplementedError("Subclasses should implement this")

    def _wrap_deriv(self, p, x, y, z=None):
        """
        Wraps the method calculating the Jacobian of the function to
        account for model constraints.

        Currently the only fitter that uses a derivative is the
        `NonLinearLSQFitter`. This wrapper may neeed to be revised
        when other fitters using function derivative are added or when
        the statistic is separated from the fitting routines.

        `~scipy.optimize.leastsq` expects the function derivative to have the
        above signature (parlist, (argtuple)). In order to
        accomodate model constraints, instead of using p directly, we set
        the parameter list in this function.

        """
        fixed_and_tied = [name for name in self.model.constraints.fixed if
                          self.model.constraints.fixed[name]]
        fixed_and_tied.extend([name for name in self.model.constraints.tied if
                               self.model.constraints.tied[name]])
        if fixed_and_tied:
            pars = self.model.constraints.modelpars
            if z is None:
                fullderiv = self.model.deriv(pars, x, y)
            else:
                fullderiv = self.model.deriv(pars, x, y, z)
            ind = range(len(self.model.param_names))
            for name in fixed_and_tied:
                index = self.model.param_names.index(name)
                ind.remove(index)
            res = np.empty((fullderiv.shape[0], fullderiv.shape[1] - len(ind)))
            res = fullderiv[:, ind]
            return res
        else:
            pars = p[:]
            if z is None:
                return self.model.deriv(pars, x, y)
            else:
                return self.model.deriv(pars, x, y, z)

    def _validate_constraints(self):
        fname = self.__class__.__name__
        try:
            c = constraintsdef[fname]
        except KeyError:
            raise UnsupportedConstraintError("{0} does not support fitting",
                                             "with constraints".format(fname))
        if any(self.model.constraints._fixed.values()) and 'fixed' not in c:
            raise ValueError("{0} cannot handle fixed parameter",
                             "constraints .".format(fname))
        if any(self.model.constraints._tied.values()) and 'tied' not in c:
            raise ValueError("{0} cannot handle tied parameter",
                             "constraints ".format(fname))
        if any(c != (-1E12, 1E12) for c in
               self.model.constraints._bounds.values()) and 'bounds' not in c:
            raise ValueError("{0} cannot handle bound parameter",
                             "constraints".format(fname))
        if self.model.constraints._eqcons and 'eqcons' not in c:
            raise ValueError("{0} cannot handle equality constraints but ",
                             "eqcons given".format(fname))
        if self.model.constraints._ineqcons and 'ineqcons' not in c:
            raise ValueError("{0} cannot handle inequality constraints but ",
                             "ineqcons given".format(fname))

    @property
    def covar(self):
        return None

    @property
    def weights(self):
        """
        Fitting weights.
        """
        return self._weights

    @weights.setter
    def weights(self, val):
        """
        Set fitting weights.
        """
        self._weights = val

    @abc.abstractmethod
    def __call__(self):
        """
        Fitters implement this method.
        It performs the actual fitting and modifies the
        parameter list of a model.

        """
        raise NotImplementedError("Subclasses should implement this")


class LinearLSQFitter(Fitter):

    """
    A class performing a linear least square fitting.

    Uses `numpy.linalg.lstsq` to do the fitting.
    Given a model and data, fits the model to the data and changes the
    model's parameters. Keeps a dictionary of auxiliary fitting information.

    Parameters
    ----------
    model : an instance of `~astropy.modeling.core.ParametricModel`

    Raises
    ------
    ModelLinearityError
        A nonlinear model is passed to a linear fitter

    """
    def __init__(self, model):
        super(LinearLSQFitter, self).__init__(model)
        if not self.model.linear:
            raise ModelLinearityError('Model is not linear in parameters, '
                                      'linear fit methods should not be used.')
        self.fit_info = {'residuals': None,
                         'rank': None,
                         'singular_values': None,
                         'pars': None
                         }

    def _deriv_with_constraints(self, pars=None, x=None, y=None):
        if y is None:
            d = self.model.deriv(x=x)
        else:
            d = self.model.deriv(x=x, y=y)
        fixed = [name for name in self.model.constraints.fixed if
                 self.model.constraints.fixed[name]]
        ind = range(len(self.model.param_names))
        for name in fixed:
            index = self.model.param_names.index(name)
            ind.remove(index)
        res = d[:, ind]
        return res

    def _map_domain_window(self, x, y=None):
        """
        Maps domain into window for a polynomial model which has
        these attributes.
        """
        if y is None:
            if hasattr(self.model, 'domain') and self.model.domain is None:
                self.model.domain = [x.min(), x.max()]
            if hasattr(self.model, 'window') and self.model.window is None:
                self.model.window = [-1, 1]
            return poly_map_domain(x, self.model.domain, self.model.window)
        else:
            if hasattr(self.model, 'x_domain') and self.model.x_domain is None:
                self.model.x_domain = [x.min(), x.max()]
            if hasattr(self.model, 'y_domain') and self.model.y_domain is None:
                self.model.y_domain = [y.min(), y.max()]
            if hasattr(self.model, 'x_window') and self.model.x_window is None:
                self.model.x_window = [-1., 1.]
            if hasattr(self.model, 'y_window') and self.model.y_window is None:
                self.model.y_window = [-1., 1.]

            xnew = poly_map_domain(x, self.model.x_domain, self.model.x_window)
            ynew = poly_map_domain(y, self.model.y_domain, self.model.y_window)
            return xnew, ynew

    def __call__(self, x, y, z=None, weights=None, rcond=None):
        """
        Fit data to this model.

        Parameters
        ----------
        x : array
            input coordinates
        y : array
            input coordinates
        z : array (optional)
            input coordinates
        weights : array (optional)
            weights
        rcond :  float, optional
            Cut-off ratio for small singular values of `a`.
            Singular values are set to zero if they are smaller than `rcond`
            times the largest singular value of `a`.
        """
        multiple = False
        x = np.asarray(x, dtype=np.float)
        y = np.asarray(y, dtype=np.float)

        if self.model.n_inputs == 2 and z is None:
            raise ValueError("Expected x, y and z for a 2 dimensional model.")

        if z is None:
            if x.shape[0] != y.shape[0]:
                raise ValueError("Expected measured and model data to have the same size")
            if y.ndim == 2:
                assert y.shape[1] == self.model._parameters.param_dim, (
                    "Number of data sets (Y array is expected to equal "
                    "the number of parameter sets")
            # map domain into window
            if hasattr(self.model, 'domain'):
                x = self._map_domain_window(x)

            if any(self.model.constraints.fixed.values()):
                lhs = self._deriv_with_constraints(x=x)
            else:
                lhs = self.model.deriv(x=x)
            if len(y.shape) == 2:
                rhs = y
                multiple = y.shape[1]
            else:
                rhs = y
        else:
            if x.shape != y.shape:
                raise ValueError("Expected x and y to have the same shape")
            if x.shape[-1] != z.shape[-1]:
                raise ValueError("x and z should have equal last dimensions")

            # map domain into window
            if hasattr(self.model, 'x_domain'):
                x, y = self._map_domain_window(x, y)

            if any(self.model.constraints.fixed.values()):
                lhs = self._deriv_with_constraints(x=x, y=y)
            else:
                lhs = self.model.deriv(x=x, y=y)
            if len(z.shape) == 3:
                rhs = np.array([i.flatten() for i in z]).T
                multiple = z.shape[0]
            else:
                rhs = z.flatten()

        if weights is not None:
            weights = np.asarray(weights, dtype=np.float)
            if len(x) != len(y):
                raise ValueError("x and weights should have the same length")
            if rhs.ndim == 2:
                lhs *= weights[:, np.newaxis]
                rhs *= weights[:, np.newaxis]
            else:
                lhs *= weights[:, np.newaxis]
                rhs *= weights

        if not multiple and self.model._parameters.param_dim > 1:
            raise ValueError("Attempting to fit a 1D data set to a model "
                             "with multiple parameter sets")
        if rcond is None:
            rcond = len(x) * np.finfo(x.dtype).eps

        scl = (lhs * lhs).sum(0)
        lacoef, resids, rank, sval = linalg.lstsq(lhs / scl, rhs, rcond)

        self.fit_info['residuals'] = resids
        self.fit_info['rank'] = rank
        self.fit_info['singular_values'] = sval

        self.model._parameters._changed = True
        # If y.n_inputs > model.n_inputs we are doing a simultanious 1D fitting
        # of several 1D arrays. Otherwise the model is 2D.
        # if y.n_inputs > self.model.n_inputs:
        if multiple:
            self.model._parameters.param_dim = multiple
        lacoef = (lacoef.T / scl).T
        self.fit_info['pars'] = lacoef
        if rank != self.model._order:
            warnings.warn("The fit may be poorly conditioned\n")
        self.fitpars = lacoef.flatten()[:]


class NonLinearLSQFitter(Fitter):

    """
    A class performing non-linear least squares fitting using the
    Levenberg-Marquardt algorithm implemented in `scipy.optimize.leastsq`.

    Parameters
    ----------
    model : a fittable :class: `~astropy.modeling.core.ParametricModel`
        model to fit to data

    Raises
    ------
    ModelLinearityError
        A linear model is passed to a nonlinear fitter

    """
    def __init__(self, model):

        self.fit_info = {'nfev': None,
                         'fvec': None,
                         'fjac': None,
                         'ipvt': None,
                         'qtf': None,
                         'message': None,
                         'ierr': None,
                         'status': None}

        super(NonLinearLSQFitter, self).__init__(model)
        if self.model.linear:
            warnings.warn('Model is linear in parameters, '
                          'consider using linear fitting methods.')

    def errorfunc(self, fps, *args):
        self.fitpars = fps
        meas = args[-1]
        if self.weights is None:
            return np.ravel(self.model(*args[: -1]) - meas)
        else:
            return np.ravel(self.weights * (self.model(*args[: -1]) - meas))

    def _set_bounds(self, fitpars):
        for c in self.model.constraints.bounds.values():
            if c != (-1E12, 1E12):
                bounds = [self.model.constraints.bounds[par] for
                          par in self.model.param_names]
                for name, par, b in zip(self.model.param_names, fitpars, bounds):
                    setattr(self.model, name, par if par > b[0] else b[0])
                    setattr(self.model, name, par if par < b[1] else b[1])

    @property
    def covar(self):
        """
        Calculate the covariance matrix
        (doesn't take into account constraints)
        """
        n = len(self.model.parameters)
        # construct the permutation matrix
        P = np.take(np.eye(n), self.fit_info['ipvt'] - 1, 0)
        # construct the R matrix as in JP = QR
        r = np.triu(self.fit_info['fjac'].T[:n, :])
        r_pt = np.dot(r, P.T)
        p_rt = np.dot(P, r.T)
        try:
            return np.dual.inv(np.dot(p_rt, r_pt))
        except:
            log.info("Could not construct a covariance matrix")
            return None

    def __call__(self, x, y, z=None, weights=None, maxiter=MAXITER, epsilon=EPS):
        """
        Fit data to this model.

        Parameters
        ----------
        x : array
           input coordinates
        y : array
           input coordinates
        z : array (optional)
           input coordinates
        weights : array (optional
           weights
        maxiter : int
            maximum number of iterations
        epsilon : float
            A suitable step length for the forward-difference
            approximation of the Jacobian (if model.fjac=None). If
            epsfcn is less than the machine precision, it is
            assumed that the relative errors in the functions are
            of the order of the machine precision.

        """
        from scipy import optimize
        x = np.asarray(x, dtype=np.float)
        self.weights = weights
        if self.model._parameters.param_dim != 1:
            # for now only single data sets ca be fitted
            raise ValueError("NonLinearLSQFitter can only fit one "
                             "data set at a time")

        if z is None:
            if x.shape[0] != y.shape[0]:
                raise ValueError("x and y should have the same shape")
            meas = np.asarray(y, dtype=np.float)
            farg = (x, meas)
        else:
            if x.shape != z.shape:
                raise ValueError("x, y and z should have the same shape")
            y = np.asarray(y, np.float)
            meas = np.asarray(z, dtype=np.float)
            farg = (x, y, meas)

        self.fitpars, status, dinfo, mess, ierr = optimize.leastsq(
            self.errorfunc, self.fitpars, args=farg, Dfun=self.dfunc,
            maxfev=maxiter, epsfcn=epsilon, full_output=True)
        self.fit_info.update(dinfo)
        self.fit_info['status'] = status
        self.fit_info['message'] = mess
        self.fit_info['ierr'] = ierr


class SLSQPFitter(Fitter):

    """
    Sequential Least Squares Programming optimization algorithm.

    The algorithm is described in [1]_. It supports tied and fixed
    parameters, as well as bounded constraints. Uses
    `scipy.optimize.slsqp`.

    Parameters
    ----------
    model : a fittable :class: `models.ParametricModel`
        model to fit to data

    Raises
    ------
    ModelLinearityError
        A linear model is passed to a nonlinear fitter

    References
    ----------
    .. [1] http://www.netlib.org/toms/733

    """
    def __init__(self, model):
        super(SLSQPFitter, self).__init__(model)
        if self.model.linear:
            warnings.warn('Model is linear in parameters, '
                          'consider using linear fitting methods.')

        self.fit_info = {'final_func_val': None,
                         'numiter': None,
                         'exit_mode': None,
                         'message': None
                         }

    def errorfunc(self, fps, *args):
        """
        Compute the sum of the squared residuals

        Parameters
        ----------
        fps : list
            parameters returned by the fitter
        args : list
            input coordinates
        """
        meas = args[-1]
        self.fitpars = fps
        res = self.model(*args[:-1]) - meas
        if self.weights is None:
            return np.sum(res ** 2)
        else:
            return np.sum(self.weights * res ** 2)

    def _set_bounds(self, fitpars):
        """
        Set this as a dummy method because the SLSQP fitter
        handles bounds internally.
        """
        pass

    def __call__(self, x, y, z=None, weights=None, verblevel=0,
                 maxiter=MAXITER, epsilon=EPS):
        """
        Fit data to this model.

        Parameters
        ----------
        x : array
            input coordinates
        y : array
            input coordinates
        z : array (optional)
            input coordinates
        weights : array (optional)
            weights
        verblevel : int
            0-silent
            1-print summary upon completion,
            2-print summary after each iteration
        maxiter : int
            maximum number of iterations
        epsilon : float
            the step size for finite-difference derivative estimates

        """
        from scipy import optimize
        x = np.asarray(x, dtype=np.float)

        self.weights = weights
        if self.model._parameters.param_dim != 1:
            # for now only single data sets ca be fitted
            raise ValueError("NonLinearLSQFitter can only fit "
                             "one data set at a time")

        if z is None:
            if x.shape[0] != y.shape[0]:
                raise ValueError("x and y should have the same shape")
            meas = np.asarray(y, dtype=np.float)
            fargs = (x, meas)
        else:
            if x.shape != z.shape:
                raise ValueError("x, y and z should have the same shape")
            y = np.asarray(y, dtype=np.float)
            meas = np.asarray(z, dtype=np.float)
            fargs = (x, y, meas)
        p0 = self.model._parameters[:]
        bounds = [self.model.constraints.bounds[par] for
                  par in self.model.param_names]
        self.fitpars, final_func_val, numiter, exit_mode, mess = optimize.fmin_slsqp(
            self.errorfunc, p0, args=fargs, disp=verblevel, full_output=1,
            bounds=bounds, eqcons=self.model.constraints.eqcons,
            ieqcons=self.model.constraints.ineqcons, iter=maxiter, acc=1.E-6,
            epsilon=EPS)
        self.fit_info['final_func_val'] = final_func_val
        self.fit_info['numiter'] = numiter
        self.fit_info['exit_mode'] = exit_mode
        self.fit_info['message'] = mess


class JointFitter(object):

    """
    Fit models which share a parameter.

    For example, fit two gaussians to two data sets but keep
    the FWHM the same.

    Parameters
    ----------
    models : list
        a list of model instances
    jointparameters : list
        a list of joint parameters
    initvals : list
        a list of initial values
    """
    def __init__(self, models, jointparameters, initvals):
        self.models = list(models)
        self.initvals = list(initvals)
        self.jointpars = jointparameters
        self._verify_input()
        for m in self.jointpars.keys():
            m.set_joint_parameters(self.jointpars[m])
        self.fitpars = self._model_to_fit_pars()

        # a list of model.n_inputs
        self.modeldims = [m.n_inputs for m in self.models]
        # sum all model dimensions
        self.ndim = np.sum(self.modeldims)

    def _model_to_fit_pars(self):
        fpars = []
        fpars.extend(self.initvals)
        for model in self.models:
            pars = model._parameters[:]
            for pname in model.joint:
                sl = model._parameters.parinfo[pname][0]
                del pars[sl]
            fpars.extend(pars)
        return fpars

    def errorfunc(self, fps, *args):
        """
        fps : list
            the fitted parameters - result of an one iteration of the
            fitting algorithm
        args : dict
            tuple of measured and input coordinates
            args is always passed as a tuple from optimize.leastsq
        """
        lstsqargs = list(args[:])
        fitted = []
        fitpars = list(fps[:])
        numjp = len(self.initvals)
        # make a separate list of the joint fitted parameters
        jointfitpars = fitpars[:numjp]
        del fitpars[:numjp]

        for model in self.models:
            margs = lstsqargs[:model.n_inputs + 1]
            del lstsqargs[:model.n_inputs + 1]
            # separate each model separately fitted parameters
            numfp = len(model._parameters) - len(model.joint)
            mfpars = fitpars[:numfp]

            del fitpars[:numfp]
            # recreate the model parameters
            mpars = []
            for pname in model.param_names:
                if pname in model.joint:
                    index = model.joint.index(pname)
                    # should do this with slices in case the
                    # parameter is not a number
                    mpars.extend([jointfitpars[index]])
                else:
                    sl = model._parameters.parinfo[pname][0]
                    plen = sl.stop - sl.start
                    mpars.extend(mfpars[:plen])
                    del mfpars[:plen]
            modelfit = model.eval(margs[:-1], mpars)
            fitted.extend(modelfit - margs[-1])
        return np.ravel(fitted)

    def _verify_input(self):
        assert(len(self.models) > 1)
        assert(len(self.jointpars.keys()) >= 2)
        for j in self.jointpars.keys():
            assert(len(self.jointpars[j]) == len(self.initvals))

    def __call__(self, *args):
        """
        Fit data to these models keeping some of the pramaters common
        to the two models.
        """
        from scipy import optimize
        assert(len(args) == reduce(lambda x, y: x + 1 + y + 1, self.modeldims))
        self.fitpars[:], _ = optimize.leastsq(self.errorfunc, self.fitpars,
                                              args=args)

        fpars = self.fitpars[:]
        numjp = len(self.initvals)
        # make a separate list of the joint fitted parameters
        jointfitpars = fpars[:numjp]
        del fpars[:numjp]

        for model in self.models:
            # extract each model's fitted parameters
            numfp = len(model._parameters) - len(model.joint)
            mfpars = fpars[:numfp]

            del fpars[:numfp]
            # recreate the model parameters
            mpars = []
            for pname in model.param_names:
                if pname in model.joint:
                    index = model.joint.index(pname)
                    # should do this with slices in case the parameter
                    # is not a number
                    mpars.extend([jointfitpars[index]])
                else:
                    sl = model._parameters.parinfo[pname][0]
                    plen = sl.stop - sl.start
                    mpars.extend(mfpars[:plen])
                    del mfpars[:plen]
            model._parameters[:] = np.array(mpars)
