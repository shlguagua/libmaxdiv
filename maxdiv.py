# coding: utf-8
#
# Detection of extreme intervals in multivariate time-series
# Author: Erik Rodner (2015-)

# Novelty detection by finding by minimizing the KL divergence
# In the following, we will derive a similar algorithm based on Kullback-Leibler (KL) divergence
# between the distribution $p_I$ of data points in the extreme interval $I = [a,b)$
# and the distribution $p_{\Omega}$ of non-extreme data points. We approximate both distributions with a simple kernel density estimate:
#
# $p_I(\mathbf{x}) = \frac{1}{|I|} \sum\limits_{i \in I} K(\mathbf{x}, \mathbf{x}_i)$
#
# with $K$ being a normalized kernel, such that $p_I$ is a proper densitity.
# Similarly, we define $p_{\Omega}$ with $\Omega = \{1, \ldots, n\} \setminus I$.

import numpy as np
import heapq
import logging
from numpy.linalg import slogdet, inv, solve
from scipy.linalg import cholesky, solve_triangular
import time
import preproc
import sys

def get_available_methods():
    return ['parzen', 'parzen_proper', 'gaussian_cov', 'gaussian_id_cov', 'gaussian_global_cov']

#
# Some helper functions for kernels
#
def enforce_multivariate_timeseries(X):
    if X.ndim==1:
        X = np.reshape(X, 1, len(X))


def calc_distance_matrix(X, metric='sqeuclidean'):
    """ Compute pairwise distances between columns in X """
    from scipy.spatial.distance import pdist, squareform
    # results from pdist are usually not stored as a symmetric matrix,
    # therefore, we use squareform to convert it
    D = squareform(pdist(X.T, 'sqeuclidean'))
    return D

def calc_gaussian_kernel(X, kernel_sigma_sq = 1.0, normalized=True):
    """ Calculate a normalized Gaussian kernel using the columns of X """
    # Let's first compute the kernel matrix from our squared Euclidean distances in $D$.
    D = calc_distance_matrix(X)
    # compute proper normalized Gaussian kernel values
    K = np.exp(-D/(2.0*kernel_sigma_sq))
    if normalized:
        K = K / np.sqrt(2*np.pi*kernel_sigma_sq)
    return K

def calc_nonstationary_gaussian_kernel(X, kernel_sigma_sq_vec):
    """ Calculate a normalized Gaussian kernel using the columns of X """
    # Let's first compute the kernel matrix from our squared Euclidean distances in $D$.
    dimension = X.shape[0]
    n = X.shape[1]
    D = calc_distance_matrix(X)
    S = np.tile(kernel_sigma_sq_vec, [n,1])
    S_sum = S + S.T
    S_prod = S * S.T
    
    # compute Gaussian kernel values
    K = np.exp(-D/(0.5*S_sum))*(np.power(S_prod,0.25)/np.sqrt(0.5*S_sum))
    return K


# Let's derive the algorithm where we try to maximize the KL divergence between the two distributions:
#
# $\text{KL}^{\alpha}(p_{\Omega}, p_I)
# = \frac{1}{n} \sum\limits_{i=1}^n p_{\Omega}(\mathbf{x}_i) \log \frac{ p_{I}^{\alpha}(\mathbf{x}_i) }{ p_{\Omega}(\mathbf{x}_i) }
# = \frac{1}{n} \sum\limits_{i=1}^n p_{\Omega}(\mathbf{x}_i) \log p_{I}^{\alpha}(\mathbf{x}_i) - \frac{1}{n} \sum\limits_{i=1}^n p_{\Omega}(\mathbf{x}_i) \log ( p_{\Omega}(\mathbf{x}_i) ) $
#
# The above formulation uses a parameterized version of the KL divergence (which will be important to get the right results).
# TODO: However, one should use something like the
# power divergence (http://link.springer.com/article/10.1007/s13571-012-0050-3) or the
# density power divergence (http://biomet.oxfordjournals.org/content/85/3/549.full.pdf).
# Plugging everything together we derive at the following algorithm:


def maxdiv_parzen(K, mode="OMEGA_I", alpha=1.0, extint_min_len = 20, extint_max_len = 150):
    """ Evaluating all possible intervals and returning the score matrix """
    # the minimal and maximal length of an extreme interval (extint_*_len)
    # this avoids trivial solutions of just one data point in the interval
    # and saves computation time

    # the index -1 represents the last element in a list/vector
    # we use the variable endelement instead to increase
    # code readability
    endelement = -1
    # compute integral sums for each column within the kernel matrix 
    K_integral = np.cumsum(K, axis=0)
    # the sum of all kernel values for each column
    # is now given in the last row
    sums_all = K_integral[endelement,:]
    # n is the number of data points considered
    n = K_integral.shape[0]

    # initialize a matrix in which we will store the KL-divergence
    # score for every possible interval
    # e.g. interval_scores[a,c] will give us the score
    # for the interval between a and a+c (including a and excluding a+c)
    interval_scores = np.zeros([n, extint_max_len])

    # small constant to avoid problems with log(0)
    eps = 1e-7

    # loop through all possible intervals from i to j
    # including i excluding j
    for i in range(n-extint_min_len):
        for j in range(i+extint_min_len, min(i+extint_max_len,n)):
            # number of data points in the current interval
            extreme_interval_length = j-i
            # number of data points outside of the current interval
            non_extreme_points = n - extreme_interval_length
            # sum up kernel values to get non-normalized
            # kernel density estimates at single points for p_I and p_Omega
            # we use the integral sums in K_integral
            # sums_extreme and sums_non_extreme are vectors of size n
            sums_extreme = K_integral[j, :] - K_integral[i, :] 
            sums_non_extreme = sums_all - sums_extreme
            # divide by the number of data points to get the final
            # parzen scores for each data point
            sums_extreme /= extreme_interval_length
            sums_non_extreme /= non_extreme_points

            # compute the KL divergence
            score = 0.0
            # the mode parameter determines which KL divergence to use
            # mode == SYM does not make much sense right now for alpha != 1.0
            if mode == "OMEGA_I" or mode == "SYM":
                # version for maximizing KL(p_Omega, p_I)
                kl_integrand1 = np.mean(np.log(sums_extreme + eps) *
                                               sums_non_extreme)
                kl_integrand2 = np.mean(np.log(sums_non_extreme + eps) *
                                               sums_non_extreme)
                negative_kl_Omega_I = alpha * kl_integrand1 - kl_integrand2
                score += - negative_kl_Omega_I

            # version for maximizing KL(p_I, p_Omega)
            if mode == "I_OMEGA" or mode == "SYM":
                kl_integrand1 = np.mean(np.log(sums_non_extreme + eps) *
                                        sums_extreme)
                kl_integrand2 = np.mean(np.log(sums_extreme + eps) *
                                        sums_extreme)
                negative_kl_I_Omega = alpha * kl_integrand1 - kl_integrand2
                score += - negative_kl_I_Omega

            # symmetrized kernel version using the mixture distribution
            if mode == "LAMBDA":
                sums_mixed = alpha * sums_extreme + (1-alpha) * sums_non_extreme
                kl_integrand1 = np.mean(np.log(sums_mixed + eps) * sums_extreme)
                kl_integrand2 = np.mean(np.log(sums_extreme + eps) * sums_extreme)
                negative_kl_I_Mixed = kl_integrand1 - kl_integrand2
                kl_integrand1 = np.mean(np.log(sums_mixed + eps) * sums_non_extreme)
                kl_integrand2 = np.mean(np.log(sums_non_extreme + eps) * sums_non_extreme)
                negative_kl_Omega_Mixed = kl_integrand1 - kl_integrand2

                score += - (alpha * negative_kl_I_Mixed + (1-alpha) * negative_kl_Omega_Mixed)

            # store the score in the matrix interval_scores
            interval_scores[i,j-i] = score

    return interval_scores


#
# Mathematically more correct version
#
def maxdiv_parzen_proper_sampling(K, mode="OMEGA_I", alpha=1.0, extint_min_len = 20, extint_max_len = 150):
    """ Evaluating all possible intervals and returning the score matrix """
    # the minimal and maximal length of an extreme interval (extint_*_len)
    # this avoids trivial solutions of just one data point in the interval
    # and saves computation time

    # the index -1 represents the last element in a list/vector
    # we use the variable endelement instead to increase
    # code readability
    endelement = -1
    # compute integral sums for each column within the kernel matrix 
    K_integral = np.cumsum(K, axis=0)
    # the sum of all kernel values for each column
    # is now given in the last row
    sums_all = K_integral[endelement,:]
    # n is the number of data points considered
    n = K_integral.shape[0]

    # initialize a matrix in which we will store the KL-divergence
    # score for every possible interval
    # e.g. interval_scores[a,c] will give us the score
    # for the interval between a and a+c (including a and excluding a+c)
    interval_scores = np.zeros([n, extint_max_len])

    # small constant to avoid problems with log(0)
    eps = 1e-7

    extreme = np.zeros(n, dtype=bool)
    non_extreme = np.ones(n, dtype=bool)
    # loop through all possible intervals from i to j
    # including i excluding j
    for i in range(n-extint_min_len):
        extreme[:] = False
        extreme[i:(i+extint_min_len)] = True
        non_extreme = np.logical_not(extreme)

        for j in range(i+extint_min_len, min(i+extint_max_len,n)):
            # number of data points in the current interval
            extreme_interval_length = j - i
            # number of data points outside of the current interval
            non_extreme_points = n - extreme_interval_length
            
            # compute the KL divergence
            score = 0.0
            # the mode parameter determines which KL divergence to use
            # mode == SYM does not make much sense right now for alpha != 1.0
            if mode == "IS_I_OMEGA":
                # for comments see OMEGA_I
                # this is a very experimental mode that exploits importance sampling
                sums_extreme = K_integral[j, :] - K_integral[i, :] 
                sums_non_extreme = sums_all - sums_extreme
                sums_extreme /= extreme_interval_length
                sums_non_extreme /= non_extreme_points
                weights = sums_extreme / (sums_non_extreme + eps)
                weights[extreme] = 1.0
                weights /= np.sum(weights)
                kl_integrand1 = np.sum(weights * np.log(sums_non_extreme + eps))
                kl_integrand2 = np.sum(weights * np.log(sums_extreme + eps))
                negative_kl_I_Omega = alpha * kl_integrand1 - kl_integrand2
                score += - negative_kl_I_Omega


            if mode == "OMEGA_I" or mode == "SYM":
                # sum up kernel values to get non-normalized
                # kernel density estimates at single points for p_I and p_Omega
                # we use the integral sums in K_integral
                # sums_extreme and sums_non_extreme are vectors of size n
                sums_extreme = K_integral[j, non_extreme] - K_integral[i, non_extreme] 
                sums_non_extreme = sums_all[non_extreme] - sums_extreme
                # divide by the number of data points to get the final
                # parzen scores for each data point
                sums_extreme /= extreme_interval_length
                sums_non_extreme /= non_extreme_points

                # version for maximizing KL(p_Omega, p_I)
                # in this case we have p_Omega 
                kl_integrand1 = np.mean(np.log(sums_extreme + eps))
                kl_integrand2 = np.mean(np.log(sums_non_extreme + eps))
                negative_kl_Omega_I = alpha * kl_integrand1 - kl_integrand2
                score += - negative_kl_Omega_I

            # version for maximizing KL(p_I, p_Omega)
            if mode == "I_OMEGA" or mode == "SYM":
                # for comments see OMEGA_I
                sums_extreme = K_integral[j, extreme] - K_integral[i, extreme] 
                sums_non_extreme = sums_all[extreme] - sums_extreme
                sums_extreme /= extreme_interval_length
                sums_non_extreme /= non_extreme_points
                kl_integrand1 = np.mean(np.log(sums_non_extreme + eps))
                kl_integrand2 = np.mean(np.log(sums_extreme + eps))
                negative_kl_I_Omega = alpha * kl_integrand1 - kl_integrand2
                score += - negative_kl_I_Omega

            # store the score in the matrix interval_scores
            interval_scores[i,j-i] = score
            if j<n:
                extreme[j] = True
                non_extreme[j] = False

    return interval_scores

#
# Maximally divergent regions using a Gaussian assumption
#
def maxdiv_gaussian_globalcov(X, mode='OMEGA_I', gaussian_mode='GLOBAL_COV', extint_min_len=20, extint_max_len=150):
    """ Evaluating all possible intervals and returning the score matrix for Gaussian
        KL divergence, we assume data points given as columns and that 
        Omega and I have the same covariance matrix """

    n = X.shape[1]
    dimension = X.shape[0]

    # simply normalize the time series beforehand
    if gaussian_mode=='GLOBAL_COV':
        cov = np.cov(X)
        if dimension==1:
            X_norm = X/np.sqrt(cov) # not really necessary
        else:
            # compute inverse "square root of cov"
            cov_chol = cholesky(cov)
            cov_chol_inv = solve_triangular(cov_chol, np.eye(cov_chol.shape[0]))
            # DEBUG print np.dot(inv(cov_chol_inv.T), inv(cov_chol_inv)) - cov
            # DEBUG sys.exit(-1)
            X_norm = np.dot( cov_chol_inv, X )
    elif gaussian_mode=='ID_COV':
        X_norm = X
    else:
        raise Exception("Unknown Gaussian mode: {}".format(gaussian_mode))

    X_integral = np.cumsum(X_norm, axis=1)

    interval_scores = np.zeros([n, extint_max_len])
    sums_all = X_integral[:, -1]

    print ("Looping through all intervals")
    start = time.time()
    eps = 1e-7
    for i in range(n-extint_min_len):
        for j in range(i+extint_min_len, min(i+extint_max_len,n)):
            extreme_interval_length = j-i
            non_extreme_points = n - extreme_interval_length
            
            sums_extreme = X_integral[:, j] - X_integral[:, i]
            sums_non_extreme = sums_all - sums_extreme
            sums_extreme /= extreme_interval_length
            sums_non_extreme /= non_extreme_points

            score = 0.0
            # the mode parameter determines which KL divergence to use
            # mode == SYM does not make much sense right now for alpha != 1.0
            diff = sums_extreme - sums_non_extreme
            score = np.sum(diff * diff)
            interval_scores[i,j-i] = score

    print ("End of optimization: {}".format(time.time() - start))
    return interval_scores


#
# Maximally divergent regions using a Gaussian assumption
#
def maxdiv_gaussian(X, mode='OMEGA_I', gaussian_mode='COV', extint_min_len=20, extint_max_len=150):
    """ Evaluating all possible intervals and returning the score matrix for Gaussian
        KL divergence, we assume data points given as columns """

    if gaussian_mode!='COV':
        return maxdiv_gaussian_globalcov(X, mode, gaussian_mode, extint_min_len, extint_max_len)

    X_integral = np.cumsum(X, axis=1)
    n = X.shape[1]
    dimension = X.shape[0]

    interval_scores = np.zeros([n, extint_max_len])
    sums_all = X_integral[:, -1]

    # compute integral series of the outer products
    # we will use this to compute covariance matrices
    print ("Computing outer products...")
    outer_X = np.apply_along_axis(lambda x: np.ravel(np.outer(x,x)), 0, X)
    outer_X_integral = np.cumsum(outer_X, axis=1)
    outer_sums_all = outer_X_integral[:, -1]

    print ("Looping through all intervals")
    start = time.time()
    eps = 1e-7
    for i in range(n-extint_min_len):
        for j in range(i+extint_min_len, min(i+extint_max_len,n)):
            extreme_interval_length = j-i
            non_extreme_points = n - extreme_interval_length
            
            sums_extreme = X_integral[:, j] - X_integral[:, i]
            sums_non_extreme = sums_all - sums_extreme
            sums_extreme /= extreme_interval_length
            sums_non_extreme /= non_extreme_points

            outer_sums_extreme = outer_X_integral[:, j] - outer_X_integral[:, i]
            outer_sums_non_extreme = outer_sums_all - outer_sums_extreme
            outer_sums_extreme /= extreme_interval_length
            outer_sums_non_extreme /= non_extreme_points

            cov_extreme = np.reshape(outer_sums_extreme, [dimension, dimension]) - \
                    np.outer(sums_extreme, sums_extreme) + eps * np.eye(dimension)
            cov_non_extreme = np.reshape(outer_sums_non_extreme, [dimension, dimension]) - \
                    np.outer(sums_non_extreme, sums_non_extreme) + eps * np.eye(dimension)
            
            #cov_extreme = np.eye(dimension)
            #cov_non_extreme = np.eye(dimension)

            _, logdet_extreme = slogdet(cov_extreme)
            _, logdet_non_extreme = slogdet(cov_non_extreme)

            score = 0.0
            # the mode parameter determines which KL divergence to use
            # mode == SYM does not make much sense right now for alpha != 1.0
            diff = sums_extreme - sums_non_extreme
            if mode == "OMEGA_I" or mode == "SYM":
                # alternative version using implicit inversion
                #kl_Omega_I = np.dot(diff, solve(cov_extreme, diff.T) )
                #kl_Omega_I += np.sum(np.diag(solve(cov_extreme, cov_non_extreme)))
                inv_cov_extreme = inv(cov_extreme)
                # term for the mahalanobis distance
                kl_Omega_I = np.dot(diff, np.dot(inv_cov_extreme, diff.T))
                # trace term
                kl_Omega_I += np.trace(np.dot(inv_cov_extreme, cov_non_extreme))
                # logdet terms
                kl_Omega_I += logdet_extreme - logdet_non_extreme
                score += kl_Omega_I

            # version for maximizing KL(p_I, p_Omega)
            if mode == "I_OMEGA" or mode == "SYM":
                inv_cov_non_extreme = inv(cov_non_extreme)
                # term for the mahalanobis distance
                kl_I_Omega = np.dot(diff, np.dot(inv_cov_non_extreme, diff.T))
                # trace term
                kl_I_Omega += np.trace(np.dot(inv_cov_non_extreme, cov_extreme))
                # logdet terms
                kl_I_Omega += logdet_non_extreme - logdet_extreme
                score += kl_I_Omega
            
            #print score, cov_extreme, cov_non_extreme, diff

            interval_scores[i,j-i] = score

    print ("End of optimization: {}".format(time.time() - start))
    return interval_scores

#
# Search non-overlapping regions
#
def find_max_regions(interval_scores, num_intervals = None, overlap_th = 0.0):
    """ Given the scores for each interval as a matrix, we select the num_intervals intervals
        which are non-overlapping and have the highest score.
        
        overlap_th specifies a threshold for non-maxima suppression: Intervals with an Intersection
        over Union (IoU) greater than this threshold will be considered overlapping.
        
        num_intervals may be set to None to retrieve all non-overlapping regions.
        
        Returns: List of 3-tuples (a, b, score), specifying the score for an interval [a,b).
                 This list will be ordered decreasingly by the score.
    """
    
    # Shortcut if only the maximum is of interest
    if num_intervals == 1:
        a, b_offs = np.unravel_index(interval_scores.argmax(), interval_scores.shape)
        return [(a, a + b_offs, interval_scores[a, b_offs])]
    
    # Retrieve indices of sorted scores
    starts, lengths = np.unravel_index(interval_scores.argsort(axis = None)[::-1], interval_scores.shape)
    
    # Non-maxima suppression
    include = np.ones(len(starts), dtype = bool) # suppressed intervals will be set to False
    found_intervals = 0
    for i in range(len(starts)):
        if include[i]:
            
            # Terminate non-maxima suppression if we already have found enough intervals
            found_intervals += 1
            if (num_intervals is not None) and (found_intervals >= num_intervals):
                include[i+1:] = False
                break
            
            # Exclude intervals with a lower score overlapping this one
            for j in range(i + 1, len(starts)):
                if include[j] and ((interval_scores[starts[j], lengths[j]] == 0) or (IoU(starts[i], lengths[i], starts[j], lengths[j]) > overlap_th)):
                    include[j] = False
    
    # Convert remaining indices to intervals
    return [(a, a + b_offs, interval_scores[a, b_offs]) for a, b_offs in zip(starts[include], lengths[include])]


def IoU(start1, len1, start2, len2):
    """ Computes the intersection over union of two intervals starting at start1 and start2 with lengths len1 and len2. """
    intersection = max(0, min(start1 + len1, start2 + len2) - max(start1, start2))
    return float(intersection) / (len1 + len2 - intersection)


def calc_max_nonoverlapping_regions(interval_scores, num_intervals, interval_min_length):
    """ Given the scores for each interval as a matrix, we greedily select the num_intervals
        intervals which are non-overlapping and have the highest score """
    # THIS METHOD DOES NOT PRODUCE CORRECT RESULTS.

    # number of data points
    n = interval_scores.shape[0]
    # interval_list will contain scored intervals
    # and we construct a priority queue to guide the selection
    interval_list = []
    heapq.heappush (interval_list, (float('-inf'), [0, n]))
    # maximum length of an interval
    interval_max_length = interval_scores.shape[1]

    # final list of regions
    regions = []
    # search for regions until we have enough + 1
    # we indeed loop through one region since it might
    # be theoretically the case that we get a better interval
    # from the second half. this is best exemplified for 
    # num_intervals = 2
    # we get the maxdiv region first and the consider the interval
    # left and right of it. we start with the left one and add it to 
    # regions, however, we should also process the right one to make sure
    # it has a smaller score
    while len(regions)<=num_intervals and len(interval_list)>0:
        # get the "best-scored" interval from the queue
        # we store negative score, since the pop always gives us 
        # the smallest element
        negative_score_all, interval = heapq.heappop(interval_list)

        a_all, b_all = interval
        max_length_within_interval = min(interval_max_length, b_all-a_all)
        min_length_within_interval = interval_min_length
        #print ("Analyzing interval ({}): {} to {}".format(-negative_score_all, a_all, b_all))
        
        # score of 0.0 would relate to equivalent distributions
        if negative_score_all == 0.0:
            #print ("Interval skipped due to zero score")
            continue

        # get the part of the interval_scores matrix we are interested in
        subseries = interval_scores[a_all:(b_all-min_length_within_interval+1), :max_length_within_interval]
        # still some impossible positions are left
        x,y = np.meshgrid(np.arange(0,subseries.shape[1],1), np.arange(0,subseries.shape[0],1))
        subseries[x+y > b_all - a_all] = 0.0

        # compute the maximum within a part of interval_scores
        a_sub, b_offset = np.unravel_index(np.argmax(subseries), subseries.shape)
        # convert the maximum position in subseries to a position
        # in interval_scores
        a = a_sub + a_all
        b = a + b_offset
        score = interval_scores[a, b_offset]
        #print ("Found region: {} to {} with score {}".format(a, b, score))
        regions.append( [a, b, score] )
        # add the interval before and the interval after to the queue
        # with the score of the current interval. maximum score within these
        # intervals is bounded from above by the score of the current interval 
        if a-a_all >= interval_min_length:
            #print ("Adding interval {} to {} with score {} to the queue".format(a_all, a, score))
            heapq.heappush(interval_list, (-score, [a_all, a]))
        if b_all-b >= interval_min_length:
            #print ("Adding interval {} to {} with score {} to the queue".format(b, b_all, score))
            heapq.heappush(interval_list, (-score, [b, b_all]))

    # sort the regions according to their score
    regions = sorted(regions, key=lambda r: r[2], reverse=True)
    # return the right number of regions
    return regions[:num_intervals]


#
# Wrapper and utility functions
#
def maxdiv(X, method = 'parzen', num_intervals=1, **kwargs):
    """ Wrapper function for calling maximum divergent regions """
    if 'preproc' in kwargs:
        if kwargs['preproc']=='local_linear':
            X = preproc.local_linear_regression(X)
        elif kwargs['preproc']=='td':
            X = preproc.td(X)
        elif not kwargs['preproc'] is None:
            raise Exception("Unknown preprocessing method {}".format(kwargs['preproc']))
        del kwargs['preproc']

    if 'kernelparameters' in kwargs:
        kernelparameters = kwargs['kernelparameters']
        del kwargs['kernelparameters']
    else:
        kernelparameters = {'kernel_sigma_sq': 1.0}

    if method == 'parzen':
        # compute kernel matrix first (Gaussian kernel)
        K = calc_gaussian_kernel(X, **kernelparameters)
        # obtain the interval [a,b] of the extreme event with score score
        interval_scores = maxdiv_parzen(K, **kwargs)

    elif method == 'parzen_proper':
        # compute kernel matrix first (Gaussian kernel)
        K = calc_gaussian_kernel(X, **kernelparameters)
        # obtain the interval [a,b] of the extreme event with score score
        interval_scores = maxdiv_parzen_proper_sampling(K, **kwargs)

    elif method.startswith('gaussian'):
        if 'alpha' in kwargs:
            del kwargs['alpha']
        kwargs['gaussian_mode'] = method[9:].upper()
        interval_scores = maxdiv_gaussian(X, **kwargs)
    else:
        raise Exception("Unknown method {}".format(method))

    xnans, ynans = np.where(np.isnan(interval_scores))
    if len(xnans)>0:
        print (xnans)
        raise Exception("NaNs found in interval_scores!")


    if 'extint_min_len' in kwargs:
        interval_min_length = kwargs['extint_min_len']
    else:
        interval_min_length = 20
    
    # get the K best non-overlapping regions
    regions = find_max_regions(interval_scores, num_intervals)

    return regions

#
# Utility functions for visualization
#
def plot_matrix_with_interval(D, a, b):
    """ Show a given kernel or distance matrix with a highlighted interval """
    import matplotlib.pylab as plt
    plt.figure()
    plt.plot(range(D.shape[0]), a*np.ones([D.shape[0],1]), 'r-')
    plt.plot(range(D.shape[0]), b*np.ones([D.shape[0],1]), 'r-')
    plt.imshow(D)
    plt.show()

def show_interval(f, a, b, visborder=100, color='b', alpha=0.3, plot_function=True, border=False):
    """ Plot a timeseries together with a marked interval """
    import matplotlib.pylab as plt
    av = max(a - visborder, 0)
    bv = min(b + visborder, f.shape[1])
    x = range(av, bv)
    minv = np.min(f[:, av:bv])
    maxv = np.max(f[:, av:bv])
    if plot_function:
        for i in range(f.shape[0]):
            plt.plot(x, f[i,av:bv], color='blue')

    if border:
        plt.plot([ a, a, b, b, a ], [minv, maxv, maxv, minv, minv], color=color, alpha=alpha, linewidth=3)
    else:
        plt.fill([ a, a, b, b ], [minv, maxv, maxv, minv], color=color, alpha=alpha)


    yborder = abs(maxv-minv)*0.05
    plt.ylim([minv-yborder, maxv+yborder])

    return x, av, bv