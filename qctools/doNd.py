import qcodes as qc
from qcodes import Station, Measurement
import qctools
import time
import numpy as np
import datetime
from threading import Thread, current_thread
from multiprocessing import Process, Event
import warnings
import sys
from IPython.display import display, clear_output

warnings.simplefilter('always', DeprecationWarning)
do1d2ddeprecationwarning = '\'do1d\' and \'do2d\' are deprecated and call the general doNd function as a variable wrapper. Please consider directly calling \'doNd\'.'

def fill_station(param_set, param_meas):
    station = Station()
    allinstr=qc.instrument.base.Instrument._all_instruments
    for key,val in allinstr.items():
        instr = qc.instrument.base.Instrument.find_instrument(key)
        station.add_component(instr)
    measparstring = ""
    for parameter in param_set:
        station.add_component(parameter)
        measparstring += parameter.name + ',' 
    for parameter in param_meas:
        station.add_component(parameter)
        measparstring += parameter.name + ',' 
    return measparstring

def safetyratesdelays(param_set,spaces):
    #Sample blowup prevention, patent pending
    for i in range(0,len(param_set)):
        if param_set[i].step == 0 or param_set[i].step == None:
            param_set[i].step = np.min(np.absolute(np.diff(spaces[i])[np.where(np.diff(spaces[i])!=0)]))
            print('Warning, \'step\' attribute for set parameter ', param_set[i].name ,' undefined. Defaulting to minimum measurement stepsize :{}'.format(param_set[i].step) )
        if param_set[i].inter_delay == 0 or param_set[i].inter_delay == None:
            param_set[i].inter_delay = 5e-2
            print('Warning, \'inter_delay\' attribute for set parameter ', param_set[i].name ,' undefined. Defaulting to \'5e-2\' s.')

def cartprod(*arrays):
    N = len(arrays)
    fullmesh = np.transpose(np.meshgrid(*arrays, indexing='ij'), 
                     np.roll(np.arange(N + 1), -1)).reshape(-1, N)
    return fullmesh

def cartprodmeander(*arrays):
    N = len(arrays)
    fullmesh = np.transpose(np.meshgrid(*arrays, indexing='ij'), 
                     np.roll(np.arange(N + 1), -1)).reshape(-1, N)
    s = int(len(fullmesh)/len(arrays[-1])/2)
    for i in range(0,s):
        fullmesh[:,-1][(2*i+1)*len(arrays[-1]):(2*i+2)*len(arrays[-1])]=fullmesh[:,-1][(2*i+1)*len(arrays[-1]):(2*i+2)*len(arrays[-1])][::-1]
    return fullmesh

def run_measurement(event, param_set, param_meas, spaces, settle_times, name, comment, meander):
    # Local reference of THIS thread object
    t = current_thread()
    # Thread is alive by default
    t.alive = True

    meas = Measurement() 

    meas.name = name

    #Generating setpoints
    if meander == True:
        setpoints = cartprodmeander(*spaces)
    else:
        setpoints = cartprod(*spaces)
    ### Filling station for snapshotting
    fill_station(param_set,param_meas)
    ### Checking and setting safety rates and delays
    safetyratesdelays(param_set,spaces)    
    
    meas.write_period = 0.1
       
    #Make array showing changes between setpoints on axes
    changesetpoints = setpoints - np.roll(setpoints, 1, axis=0)

    #Forcing the first setpoint in changesetpoints to 1 to make sure it is always set.
    changesetpoints[0,:] = 1
   
    ### Registering measurement parameters
    for parameter in param_set:
        meas.register_parameter(parameter)
        #param_set[i].post_delay = delay[i]
    output = [] 
    for parameter in param_meas:
        #print(param_set)
        meas.register_parameter(parameter, setpoints=(*param_set,))
        output.append([parameter, None])   

    with meas.run() as datasaver:  
        global measid
        measid = datasaver.run_id
        print(measid)

        starttime = datetime.datetime.now()
        lastwrittime = starttime
        startstring = ' Started - ' + starttime.strftime('%Y-%m-%d %H:%M:%S')
        print(startstring)  
        #Getting dimensions and array dimensions and lengths
        ndims = int(len(spaces))
        lenarrays = np.zeros(len(spaces))
        for i in range(0,len(spaces)):
            lenarrays[i] = int(len(spaces[i]))
            #Main loop for setting values
        for i in range(0,len(setpoints)):
            #Check for nonzero axis to apply new setpoints
            resultlist = [None]*ndims
            for j in reversed(range(0,ndims)):
                if not np.isclose(changesetpoints[i,j] , 0):
                    param_set[j].set(setpoints[i,j])
                    time.sleep(settle_times[j])
                for k, parameter in enumerate(param_meas):
                    output[k][1] = parameter.get()                
                resultlist[j] = (param_set[j],setpoints[i,j])
            datasaver.add_result(*resultlist,
                                 *output)
            # If alive is set to false
            if not t.alive:
                event.set()
                qctools.db_extraction.db_extractor(dbloc = qc.dataset.sqlite.database.get_DB_location(), 
                                   ids=[measid], 
                                   overwrite=True,
                                   newline_slowaxes=True,
                                   no_folders=False,
                                   suppress_output=True)
                raise KeyboardInterrupt('User interrupted doNd. All data flushed to database and extracted to *.dat file.')
                # Break out of for loop
                break
            #Time estimation
            frac_complete = (i+1)/len(setpoints)
            duration_in_sec = (datetime.datetime.now()-starttime).total_seconds()/frac_complete
            elapsed_in_sec = (datetime.datetime.now()-starttime).total_seconds()
            remaining_in_sec = duration_in_sec-elapsed_in_sec
            perc_complete = np.round(100*frac_complete,2)
            progressstring = 'Setpoint ' + str(i) + ' of ' + str(len(setpoints)) + ', ' + str(perc_complete) + ' % complete.'
            durationstring = '      Total duration - ' + str(datetime.timedelta(seconds=np.round(duration_in_sec)))
            elapsedstring =  '        Elapsed time - ' +  str(datetime.timedelta(seconds=np.round(elapsed_in_sec)))
            remainingstring ='      Remaining time - ' + str(datetime.timedelta(seconds=np.round(remaining_in_sec)))
            etastring =      '     ETA - ' + str((datetime.timedelta(seconds=np.round(duration_in_sec))+starttime).strftime('%Y-%m-%d %H:%M:%S'))
            #etastring = ' ETA: ' + str(datetime.timedelta(seconds=duration_in_sec+starttime.total_seconds()))
            totalstring = progressstring + '\n' + startstring + '\n' +  etastring + '\n' + durationstring + '\n' + elapsedstring + '\n' + remainingstring 
            clear_output(wait=True)
            print(totalstring)
            datasaver.dataset.add_metadata('Comment', comment)
        finishstring =   'Finished - ' + str((datetime.datetime.now()).strftime('%Y-%m-%d %H:%M:%S'))
        print(finishstring)
        event.set()
            #endtime = datetime.datetime.now()
            #if space1.tolist().index(set_point1) is 0: #Print the time taken for the first inner run
            #   print('First Inner Run Finished at ' + datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                #Run db_extractor after fast axes is finished

def run_dbextractor(event,dbextractor_write_interval):
    #Controls how often the measurement is written to *.dat file
    lastwrittime = datetime.datetime.now()
    while event.is_set()==False:
        if (datetime.datetime.now()-lastwrittime).total_seconds() > dbextractor_write_interval and measid is not None:
            try:
                qctools.db_extraction.db_extractor(dbloc = qc.dataset.sqlite.database.get_DB_location(), 
                                                   ids=[measid], 
                                                   overwrite=True,
                                                   newline_slowaxes=True,
                                                   no_folders=False,
                                                   suppress_output=True)
                lastwrittime = datetime.datetime.now()
            except:
                pass
        time.sleep(1)

def testfunc():
    for i in range(0,10):
        print('testfunc',i)
        time.sleep(0.5)

def doNd(param_set, spaces, settle_times, param_meas, name='', comment='', meander=False):
    # And then run an experiment
    global measid
    measid = None
        
    if __name__ is not '__main__':
        #Create Event
        event = Event()
        stopthread = Event()
        p1 = Thread(target = run_measurement, args=(event, param_set, param_meas, spaces, settle_times, name, comment, meander))
        dbextractor_write_interval = 30 #sec
        p2 = Thread(target = run_dbextractor, args=(event,dbextractor_write_interval))
        p1.daemon = True
        p2.daemon = True
        try:
                # Start the thread
            p1.start()
            p2.start()
            # If the child thread is still running
            while p1.is_alive():
                # Try to join the child thread back to parent for 0.5 seconds
                p1.join(0.5)
                p2.join(0.5)
        # When ctrl+c is received
        except KeyboardInterrupt as e:
            # Set the alive attribute to false
            p1.alive = False
            p2.alive = False
            # Block until child thread is joined back to the parent
            p1.join()
            p2.join()
            # Exit with error code
            sys.exit(e)
        #p1.start()
        #p2.start()
        #p1.join()
        #p2.join()
    qctools.db_extraction.db_extractor(dbloc = qc.dataset.sqlite.database.get_DB_location(), 
                                       ids=[measid], 
                                       overwrite=True,
                                       newline_slowaxes=True,
                                       no_folders=False,
                                       suppress_output=True)
    return measid

#More advanced do1d
#Waits for a time given by settle_time after setting the setpoint

def do1d_settle(param_set, space, settle_time, delay=None, param_meas=[], name='', comment=''):
    warnings.warn(do1d2ddeprecationwarning, DeprecationWarning)
    param_set = [param_set]
    param_meas = param_meas
    spaces = [space]
    settle_times = [settle_time]
    if delay is not None:
        warnings.warn('Use of \'delay\' is deprecated, sweep rates are controlled by instruments and \'settle_time\' is used for measurement delays.')
    measid = doNd(param_set, spaces, settle_times, param_meas, name='', comment='', meander=False)
    return measid

def do1d(param_set, start, stop, num_points, delay=None, param_meas=[], name='', comment=''):
    warnings.warn(do1d2ddeprecationwarning, DeprecationWarning)
    param_set = [param_set]
    param_meas = param_meas
    spaces = [np.linspace(start,stop,num_points)]
    if delay is not None:
        warnings.warn('Use of \'delay\' is deprecated and is used as \'settle_time\' in this function.', DeprecationWarning)
        settle_times = [delay]
    else:
        settle_times = [1e-3]
    measid = doNd(param_set, spaces, settle_times, param_meas, name='', comment='', meander=False)
    return measid

def do2d(param_set1, start1, stop1, num_points1, param_set2,  start2, stop2, num_points2,delay1=None, delay2=None, 
    param_meas=[], name='', comment='', fasttozero=None):
    warnings.warn(do1d2ddeprecationwarning, DeprecationWarning)
    param_set = [param_set1, param_set2]
    param_meas = param_meas
    spaces = [np.linspace(start1, stop1, num_points1), np.linspace(start1, stop1, num_points1)]
    if delay1 or delay2 is not None:
        warnings.warn('Use of \'delay\' is deprecated and is used as \'settle_time\' in this function.', DeprecationWarning)
        settle_times = [delay1,delay2]
    else:
        settle_times = [1e-3,1e-3]
    measid = doNd(param_set, spaces, settle_times, param_meas, name='', comment='', meander=False)
    return measid

#More advanced do2d
#Modified for custom resolution and to wait for settle_time time after every time set_point is set
#e.g.    space1 = np.concatenate(([1, 2], np.arange(2.5,6.1,0.5), [6.1, 6.2, 6.25, 6.3, 6.5]))
#        space2 = np.linspace(-2e-6, 2e-6, 1000)
def do2d_settle(param_set1, space1, settle_time1, param_set2, space2, settle_time2, delay1=None, delay2=None, param_meas=[], name='', comment='', fasttozero=None):
    warnings.warn(do1d2ddeprecationwarning, DeprecationWarning)
    param_set = [param_set1, param_set2]
    param_meas = param_meas
    spaces = [space1, space2]
    settle_times = [settle_time1, settle_time2]
    if delay1 or delay2 is not None:
        warnings.warn('Use of \'delay\' is deprecated, sweep rates are controlled by instruments and \'settle_time\' is used for measurement delays.', DeprecationWarning)
    measid = doNd(param_set, spaces, settle_times, param_meas, name='', comment='', meander=False)
    return measid

#Some general lock-in specific functions used in the doND_settle functions
def change_sensitivity_AP(self, dn):
    _ = self.sensitivity.get()
    n = int(self.sensitivity.raw_value)
    if self.input_config() in ['a', 'a-b']:
        n_to = self._N_TO_VOLT
    else:
        n_to = self._N_TO_CURR

    if n + dn > max(n_to.keys()) or n + dn < min(n_to.keys()):
        return False

    self.sensitivity.set(n_to[n + dn])
    return True

def increment_sensitivity_AP(self):
    """
    Increment the sensitivity setting of the lock-in. This is equivalent
    to pushing the sensitivity up button on the front panel. This has no
    effect if the sensitivity is already at the maximum.

    Returns:
    Whether or not the sensitivity was actually changed.
    """
    return change_sensitivity_AP(lockin, 1)


def decrement_sensitivity_AP(self):
    """
    Increment the sensitivity setting of the lock-in. This is equivalent
    to pushing the sensitivity up button on the front panel. This has no
    effect if the sensitivity is already at the maximum.

    Returns:
    Whether or not the sensitivity was actually changed.
    """
    return change_sensitivity_AP(lockin, -1)

def auto_sensitivity():
    if np.abs(diff_resistance.get()[2]) <= 0.2*lockin.sensitivity.get() and lockin.sensitivity.get() > 500e-9:
        decrement_sensitivity_AP(lockin)
        time.sleep(3*lockin.time_constant.get())
    elif np.abs(diff_resistance.get()[2]) >= 0.9*lockin.sensitivity.get():
        increment_sensitivity_AP(lockin)
        time.sleep(3*lockin.time_constant.get())