# Copyright (c) 2016, Mike Smith
# Licensed under the BSD 3-clause license (see LICENSE.txt)
import GPy
import numpy as np
import sys #so I can print dots
import time
import os
from joblib import Parallel, delayed

def get_log_likelihood(inputs,data,clust):
    """Get the LL of a combined set of clusters, ignoring time series offsets.

    Get the log likelihood of a cluster without worrying about the fact
    different time series are offset. We're using it here really for those
    cases in which we only have one cluster to get the loglikelihood of.

    arguments:
    inputs -- the 'X's in a list, one item per cluster
    data -- the 'Y's in a list, one item per cluster
    clust -- list of clusters to use

    returns a tuple:
    log likelihood and the offset (which is always zero for this model)
    """

    S = data[0].shape[0] #number of time series

    #build a new dataset from the clusters, by combining all clusters together
    X = np.zeros([0,1])
    Y = np.zeros([0,S])

    #for each person in the cluster,
    #add their inputs and data to the new dataset
    for p in clust:
        X = np.vstack([X,inputs[p]])
        Y = np.vstack([Y,data[p].T])

    #find the loglikelihood. We just add together the LL for each time series.
    #ll=0
    #for s in range(S):
    #    m = GPy.models.GPRegression(X,Y[:,s][:,None])
    #    m.optimize()
    #    ll+=m.log_likelihood()

    m = GPy.models.GPRegression(X,Y)
    m.optimize()
    ll=m.log_likelihood()
    return ll,0

def get_log_likelihood_offset(inputs,data,clust):
    """Get the log likelihood of a combined set of clusters, fitting the offsets

    arguments:
    inputs -- the 'X's in a list, one item per cluster
    data -- the 'Y's in a list, one item per cluster
    clust -- list of clusters to use

    returns a tuple:
    log likelihood and the offset
    """

    #if we've only got one cluster, the model has an error, so we want to just
    #use normal GPRegression.
    if len(clust)==1:
        return get_log_likelihood(inputs,data,clust)

    S = data[0].shape[0] #number of time series

    X = np.zeros([0,2]) #notice the extra column, this is for the cluster index
    Y = np.zeros([0,S])

    #for each person in the cluster, add their inputs and data to the new
    #dataset. Note we add an index identifying which person is which data point.
    #This is for the offset model to use, to allow it to know which data points
    #to shift.
    for i,p in enumerate(clust):
        idx = i*np.ones([inputs[p].shape[0],1])
        X = np.vstack([X,np.hstack([inputs[p],idx])])
        Y = np.vstack([Y,data[p].T])

    m = GPy.models.GPOffsetRegression(X,Y)
    #TODO: How to select a sensible prior?
    m.offset.set_prior(GPy.priors.Gaussian(0,20))
    #TODO: Set a sensible start value for the length scale,
    #make it long to help the offset fit.

    m.optimize()

    ll = m.log_likelihood()
    offset = m.offset.values[0]
    return ll,offset

def cluster_loglike(clusti,data,inputs,pairloglikes):
        results = [get_log_likelihood_offset(inputs,data,[clusti,clustj])  for clustj in range(clusti) if np.isnan(pairloglikes[clusti,clustj])]
        return(results)

def cluster(data,inputs,verbose=False,parallel=False,cores=1,batch=1):
    """Clusters data

    Using the new offset model, this method uses a greedy algorithm to cluster
    the data. It starts with all the data points in separate clusters and tests
    whether combining them increases the overall log-likelihood (LL). It then
    iteratively joins pairs of clusters which cause the greatest increase in
    the LL, until no join increases the LL.

    arguments:
    inputs -- the 'X's in a list, one item per cluster
    data -- the 'Y's in a list, one item per cluster
    verbose -- True or False. If True, gives additional information on the algorithm.
    parallel -- True or False. If True, runs the algorithm in paralllel using joblib
    cores -- How many cores to parallelise over. Ignored if parallel is False
    batch -- A joblib parallelisation argument. Ignored if parallel is False.

    returns a list of the clusters.
    """
    N=len(data)
    if verbose & parallel:
        try:
            omp_num_threads = os.environ['OMP_NUM_THREADS']
        except KeyError:
            omp_num_threads = 'unset'
        print('parallel processing')
        print('{0} cores : This is the number of cores your job will be split over'.format(cores))
        print('{0} batch size : The number of atomic tasks to dispatch at once to each worker. '.format(batch))
        print('{0} OMP_NUM_THREADS : An environment variable that affects number of cores for OpenMP-accelerated functions. (e.g.Intel MKL)'.format(omp_num_threads))
    if verbose & (not parallel):
        print('serial processing')

    #Define a set of N active cluster
    active = []
    for p in range(0,N):
        active.append([p])

    loglikes = np.zeros(len(active))
    loglikes[:] = None

    pairloglikes = np.zeros([len(active),len(active)])
    pairloglikes[:] = None
    pairoffset = np.zeros([len(active),len(active)])

    it = 0
    with Parallel(n_jobs=cores,batch_size=batch) as parallel_pool:
        while True:

            if verbose:
                it +=1
                print("Iteration %d" % it)
                t0 = time.time()

            if parallel:
                    #results = [cluster_loglike(clusti,data,inputs,loglikes,pairloglikes) for clusti in range(len(active))]
                    results = parallel_pool(delayed(cluster_loglike)(vari,data,inputs,pairloglikes) for vari in range(len(active)))
                    #results is a list of lists that needs flattening
                    results=[item for sublist in results for item in sublist]

                    #Make the assignments
                    results_index = 0
                    for clusti in range(len(active)):
                        if np.isnan(loglikes[clusti]):
                            loglikes[clusti], unused_offset = get_log_likelihood_offset(inputs,data,[clusti])
                        for clustj in range(clusti): #count from 0 to clustj-1
                            if np.isnan(pairloglikes[clusti,clustj]):
                                pairloglikes[clusti,clustj],pairoffset[clusti,clustj] = results[results_index]
                                results_index = results_index + 1
            else: #serial
                for clusti in range(len(active)):
                                if verbose:
                                    sys.stdout.write('.')
                                    sys.stdout.flush()
                                if np.isnan(loglikes[clusti]):
                                    loglikes[clusti], unused_offset = get_log_likelihood_offset(inputs,data,[clusti])
                                    #try combining with each other cluster...
                                for clustj in range(clusti): #count from 0 to clustj-1
                                    temp = [clusti,clustj]
                                    if np.isnan(pairloglikes[clusti,clustj]):
                                        pairloglikes[clusti,clustj],pairoffset[clusti,clustj] = get_log_likelihood_offset(inputs,data,temp)

            seploglikes = np.repeat(loglikes[:,None].T,len(loglikes),0)+np.repeat(loglikes[:,None],len(loglikes),1)
            loglikeimprovement = pairloglikes - seploglikes #how much likelihood improves with clustering
            top = np.unravel_index(np.nanargmax(pairloglikes-seploglikes), pairloglikes.shape)

            #if loglikeimprovement.shape[0]<3:
            # #no more clustering to do - this shouldn't happen really unless
            # #we've set the threshold to apply clustering to less than 0
            #    break

            #if theres further clustering to be done...
            if loglikeimprovement[top[0],top[1]]>0:
                active[top[0]].extend(active[top[1]])
                offset=pairoffset[top[0],top[1]]
                inputs[top[0]] = np.vstack([inputs[top[0]],inputs[top[1]]-offset])
                data[top[0]] = np.hstack([data[top[0]],data[top[1]]])
                del inputs[top[1]]
                del data[top[1]]
                del active[top[1]]

                #None = message to say we need to recalculate
                pairloglikes[:,top[0]] = None
                pairloglikes[top[0],:] = None
                pairloglikes = np.delete(pairloglikes,top[1],0)
                pairloglikes = np.delete(pairloglikes,top[1],1)
                loglikes[top[0]] = None
                loglikes = np.delete(loglikes,top[1])
                if verbose:
                    t1 = time.time()
                    print('\nloop time',t1-t0)
            else:
                break

        #if loglikeimprovement[top[0],top[1]]>0:
        #    print "joined"
        #    print top
        #    print offset
        #    print offsets
        #    print offsets[top[1]]-offsets[top[0]]

    #TODO Add a way to return the offsets applied to all the time series
    return active

#starttime = time.time()
#active = cluster(data,inputs)
#endtime = time.time()
#print "TOTAL TIME %0.4f" % (endtime-starttime)
