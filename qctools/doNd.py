import qcodes as qc
from qcodes import Station, Measurement
from qcodes.dataset.plotting import plot_by_id
import qctools
import time
import numpy as np
import datetime
from threading import Thread, current_thread
from multiprocessing import Process, Event
import warnings
import sys
from IPython.display import display, clear_output
from tabulate import tabulate

# function to get unique values 
def unique(list1): 
  
    # intilize a null list 
    unique_list = [] 
      
    # traverse for all elements 
    for x in list1: 
        # check if exists in unique_list or not 
        if x not in unique_list: 
            unique_list.append(x) 
    return unique_list 

def fill_station(param_set, param_meas):
    to=1
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
        try: # Prevent station crash when component of parameter is not unique
            station.add_component(parameter)
            measparstring += parameter.name + ',' 
        except Exception as e:
            print('Error ignored when filling station: \n', e)
            pass
    return measparstring

def fill_station_zerodim(param_meas):
    station = Station()
    allinstr=qc.instrument.base.Instrument._all_instruments
    for key,val in allinstr.items():
        instr = qc.instrument.base.Instrument.find_instrument(key)
        station.add_component(instr)
    measparstring = ""
    for parameter in param_meas:
        station.add_component(parameter)
        measparstring += parameter.name + ',' 
    return measparstring

def safetyratesdelays(param_set,spaces):
    #Sample blowup prevention, patent pending, checking and correcting step and inter_delay for all set parameters 
    for i in range(0,len(param_set)):
        if param_set[i].step == 0 or param_set[i].step == None:
            if len(spaces[i])>1:
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
        # Most unreadable line of code in the world, but it does the meandering of the setpoints somehow...
        fullmesh[:,-1][(2*i+1)*len(arrays[-1]):(2*i+2)*len(arrays[-1])]=fullmesh[:,-1][(2*i+1)*len(arrays[-1]):(2*i+2)*len(arrays[-1])][::-1]
    return fullmesh

def run_measurement(event, 
                    param_set, 
                    param_meas, 
                    spaces, 
                    settle_times, 
                    name, 
                    comment, 
                    meander, 
                    extra_cmd, 
                    extra_cmd_val,
                    wait_first_datapoint,
                    checkstepinterdelay,
                    manualsetpoints,
                    snapshot):
    # Local reference of THIS thread object
    t = current_thread()
    # Thread is alive by default
    t.alive = True

    # Create measurement object
    meas = Measurement() 
    # Apply name
    meas.name = name

    #Generating setpoints
    if manualsetpoints==False:
        if meander == True:
            setpoints = cartprodmeander(*spaces)
        else:
            setpoints = cartprod(*spaces)
    else:
        setpoints = spaces
    ### Filling station for snapshotting
    if snapshot == True:
        fill_station(param_set,param_meas)
    ### Checking and setting safety rates and delays
    if checkstepinterdelay:
        safetyratesdelays(param_set,spaces)    
    
    meas.write_period = 1
       
    #Make array showing changes between setpoints on axes
    changesetpoints = setpoints - np.roll(setpoints, 1, axis=0)

    #Forcing the first setpoint in changesetpoints to 1 to make sure it is always set.
    changesetpoints[0,:] = 1
   
    # Registering set parameters
    param_setstring = ''
    param_setnames = [None]*len(param_set)
    param_setunits = [None]*len(param_set)
    for i,parameter in enumerate(param_set):
        meas.register_parameter(parameter)
        param_setstring += parameter.name + ', '
        param_setnames[i] = parameter.name
        param_setunits[i] = parameter.unit
    
    output = [None]*len(param_meas) 
        # Registering readout parameters
    param_measstring = ''
    param_measnames = [None]*len(param_meas)
    param_measunits = [None]*len(param_meas)
    param_measnames_sub = [None]*len(param_meas)
    paramtype = [None]*len(param_meas)
    for i, parameter in enumerate(param_meas):
        meas.register_parameter(parameter, setpoints=(*param_set,))
        output[i]= [parameter, None]
        param_measstring += parameter.name + ', '
        param_measnames[i] = parameter.name
        if isinstance(parameter, qc.instrument.ParameterWithSetpoints):
            param_measunits[i] = parameter.unit
            param_measnames_sub[i] = ''
            paramtype[i] = 'ParameterWithSetpoints'
        elif isinstance(parameter, qc.instrument.MultiParameter):
            param_measunits[i] = parameter.units
            param_measnames_sub[i] = parameter.names
            paramtype[i] = 'MultiParameter'
        elif isinstance(parameter, qc.instrument.Parameter):
            param_measunits[i] = parameter.unit
            paramtype[i] = 'Parameter'

    # Start measurement routine
    with meas.run() as datasaver:  
        global measid
        measid = datasaver.run_id

        # Getting dimensionality of measurement
        ndims = setpoints.shape[1]
        
        # Add comment to metadata in database
        datasaver.dataset.add_metadata('Comment', comment)
        
        # Main loop for setting values
        for i in range(0,setpoints.shape[0]):
            #Check for nonzero axis to apply new setpoints by looking in changesetpoints arrays
            resultlist = [None]*ndims
            if i==0: #On first datapoint change set_params from slow to fast axis
                dimlist = range(0,ndims)
            else: #On all other datapoints change fast axis first
                dimlist = reversed(range(0,ndims))
            for j in dimlist:
                if not np.isclose(changesetpoints[i,j] , 0, atol=0): # Only set set params that need to be changed
                    if i==0 and not t.alive: # Allows killing of thread in-between initialisiation of set_parameters for first datapoint.
                        event.set() # Trigger closing of run_dbextractor
                        raise KeyboardInterrupt('User interrupted doNd during initialisation of first setpoint.')
                        # Break out of for loop
                        break
                    param_set[j].set(setpoints[i,j])
                    time.sleep(settle_times[j]) # Apply appropriate settle_time
                resultlist[j] = (param_set[j],setpoints[i,j]) # Make a list of result
            if i==0: # Add additional waiting time for first measurement point before readout and start timers
                time.sleep(wait_first_datapoint)
                # Start various timers
                starttime = datetime.datetime.now() + datetime.timedelta(0,-1)
                lastwrittime = starttime
                lastprinttime = starttime             
            for k, parameter in enumerate(param_meas): # Readout all measurement parameters at this setpoint i
                if extra_cmd is not None: # Optional extra command + value that is run before each measurement paremeter is read out.
                    if extra_cmd[k] is not None:
                        if extra_cmd_val is not None: 
                            if extra_cmd_val[k] is not None:
                                (extra_cmd[k])(extra_cmd_val[k])
                            else:
                                (extra_cmd[k])()
                        else:
                            (extra_cmd[k])()
                output[k][1] = parameter.get()
            datasaver.add_result(*resultlist, # Add everything to the database
                                 *output)
            setvals = list(zip(param_setnames,[f"{x:.{6}}" for x in setpoints[i,:]],param_setunits))
            outputparsed = [None]*len(param_meas)
            for k,x in enumerate([row[1] for row in output]):
                if paramtype[k] == 'MultiParameter':
                    valsparsed = [None]*len(x)
                    for l,y in enumerate(x):
                        if isinstance(y, (list,tuple,np.ndarray)):
                            if len(y) > 5:
                                vals = ['{:.6f}'.format(x) for x in y[0:5]]
                                vals.append('.......')
                            else:
                                vals = ['{:.6f}'.format(x) for x in y]
                            newvals = [[vals[i]] for i in range(0,len(vals))]
                            valsparsed[l] = tabulate(newvals,tablefmt='plain') 
                        else:
                            valsparsed[l] = f"{y:.{6}}"
                    outputparsed[k] = tabulate(list(zip(param_measnames_sub[k],valsparsed,param_measunits[k])), tablefmt='plain', colalign=('left','left','left'))
                if paramtype[k] == 'Parameter':
                    outputparsed[k] = tabulate([[f"{float(x):.{6}}",param_measunits[k]]], tablefmt='plain')
                if paramtype[k] == 'ParameterWithSetpoints':
                    outputparsed[k] = '{Parameter with setpoints, not shown.}'
            measvals = list(zip(param_measnames,outputparsed))

            if not t.alive: # Check if user tried to kill the thread by keyboard interrupt, if so kill it
                event.set() # Trigger closing of run_dbextractor
                qctools.db_extraction.db_extractor(dbloc = qc.dataset.sqlite.database.get_DB_location(),  # Run db_extractor once more
                                   ids=[measid], 
                                   overwrite=True,
                                   newline_slowaxes=True,
                                   no_folders=False,
                                   suppress_output=True,
                                   useopendbconnection = True)
                plot_by_id(measid)
                raise KeyboardInterrupt('User interrupted doNd. All data flushed to database and extracted to *.dat file.')
                # Break out of for loop
                break
            #Time estimation
            printinterval = 0.025 # Increase printinterval to save CPU
            now = datetime.datetime.now()
            finish =['','']
            if (now-lastprinttime).total_seconds() > printinterval or i == len(setpoints)-1: # Calculate and print time estimation
                frac_complete = (i+1)/len(setpoints)
                duration_in_sec = (now-starttime).total_seconds()/frac_complete
                elapsed_in_sec = (now-starttime).total_seconds()
                remaining_in_sec = duration_in_sec-elapsed_in_sec
                perc_complete = np.round(100*frac_complete,2)
                clear_output(wait=True)
                if i == len(setpoints)-1:
                    finish[0] = 'Finished: ' + str((now).strftime('%Y-%m-%d'))
                    finish[1] = str((now).strftime('%H:%M:%S'))

                l1 = tabulate([['----------------------' ,'-------------------------------------------------'],
                               ['Starting runid:', str(measid)], # Time estimation now in properly aligned table format
                               ['Name:', name], 
                               ['Comment:', comment],
                               ['Set parameter(s):', tabulate(setvals, tablefmt='plain', colalign=('left','left','left'))],
                               ['Readout parameter(s):', tabulate(measvals, tablefmt='plain', colalign=('left','left'))],
                               ['______________________' ,'_________________________________________________'],
                               ['Setpoint: ' + str(i+1) + ' of ' + str(len(setpoints)), '%.2f' % perc_complete + ' % complete.'],
                               ['Started: ' + starttime.strftime('%Y-%m-%d'), starttime.strftime('%H:%M:%S')],
                               ['ETA: ' + str((datetime.timedelta(seconds=np.round(duration_in_sec))+starttime).strftime('%Y-%m-%d')), str((datetime.timedelta(seconds=np.round(duration_in_sec))+starttime).strftime('%H:%M:%S'))],
                               [finish[0],finish[1]],
                               ['Total duration:', str(datetime.timedelta(seconds=np.round(duration_in_sec)))],
                               ['Elapsed time:', str(datetime.timedelta(seconds=np.round(elapsed_in_sec)))],
                               ['Remaining time:', str(datetime.timedelta(seconds=np.round(remaining_in_sec)))],
                               ], colalign=('right','left'), tablefmt='plain')
                print(l1)
                lastprinttime = now
        event.set() # Trigger closing of run_dbextractor

def run_zerodim(event, param_meas, name, comment, wait_first_datapoint,snapshot):
    # Local reference of THIS thread object
    t = current_thread()
    # Thread is alive by default
    t.alive = True

    # Create measurement object
    meas = Measurement() 
    # Apply name
    meas.name = name

    ### Filling station for snapshotting
    if snapshot == True:
        fill_station_zerodim(param_meas)
    
    meas.write_period = 0.5
    output = [] 
    # Registering readout parameters
    param_measstring = ''
    for parameter in param_meas:
        meas.register_parameter(parameter)
        output.append([parameter, None])   
        param_measstring += parameter.name + ', '
    
    # Start measurement routine
    with meas.run() as datasaver:  
        global measid
        measid = datasaver.run_id

        # Start various timers
        starttime = datetime.datetime.now()
        l1 = tabulate([['----------------------' ,'-------------------------------------------------'],
                       ['Running 0-dimensional measurement,', 'time estimation not available.'], # Time estimation now in properly aligned table format
                       ['Starting runid:', str(measid)], # Time estimation now in properly aligned table format
                       ['Name:', name], 
                       ['Comment:', comment],
                       ['Starting runid:', str(measid)],
                       ['Readout parameter(s):', str(param_measstring)],
                       ['______________________' ,'_________________________________________________'],
                       ['Started: ' + starttime.strftime('%Y-%m-%d'), starttime.strftime('%H:%M:%S')],
                       ], colalign=('right','left'), tablefmt='plain')
        print(l1)

        # Getting dimensions and array dimensions and lengths
        # Main loop for setting values
        #Check for nonzero axis to apply new setpoints by looking in changesetpoints arrays
        time.sleep(wait_first_datapoint)
        resultlist = [None]*1
        for k, parameter in enumerate(param_meas): # Readout all measurement parameters at this setpoint i
                output[k][1] = parameter.get()                
        datasaver.add_result(*output)
        datasaver.dataset.add_metadata('Comment', comment) # Add comment to metadata in database
        now = datetime.datetime.now()
        elapsed_in_sec = (now-starttime).total_seconds()
        clear_output(wait=True)
        l1 = tabulate([['---------------------------------' ,'-------------------------------------------'],
                       ['Running 0-dimensional measurement,', 'time estimation not available.'], # Time estimation now in properly aligned table format
                       ['Starting runid:', str(measid)], # Time estimation now in properly aligned table format
                       ['Name:', name], 
                       ['Comment:', comment],
                       ['Starting runid:', str(measid)],
                       ['Readout parameter(s):', str(param_measstring)],
                       ['_________________________________' ,'___________________________________________'],
                       ['Started: ' + starttime.strftime('%Y-%m-%d'), starttime.strftime('%H:%M:%S')],
                       ['Finished: ' + str((now).strftime('%Y-%m-%d')),str((now).strftime('%H:%M:%S'))],
                       ['Total duration:', str(datetime.timedelta(seconds=np.round(elapsed_in_sec)))],
                       ], colalign=('right','left'), tablefmt='plain')
        print(l1)
        event.set() # Trigger closing of run_dbextractor

def run_dbextractor(event,dbextractor_write_interval):
    #Controls how often the measurement is written to *.dat file
    lastwrittime = datetime.datetime.now()
    while event.is_set()==False:
        timepassedsincelastwrite = (datetime.datetime.now()-lastwrittime).total_seconds()
        if timepassedsincelastwrite > dbextractor_write_interval and measid is not None:
            if timepassedsincelastwrite > 1.5*dbextractor_write_interval:
                time.sleep(3*timepassedsincelastwrite)
            qctools.db_extraction.db_extractor(dbloc = qc.dataset.sqlite.database.get_DB_location(), 
                                               ids=[measid], 
                                               overwrite=True,
                                               newline_slowaxes=True,
                                               no_folders=False,
                                               suppress_output=True,
                                               useopendbconnection = True)
            lastwrittime = datetime.datetime.now()
            #except:
            #    pass
        time.sleep(dbextractor_write_interval/10)

def doNd(param_set, 
         spaces, 
         settle_times, 
         param_meas, 
         name='', 
         comment='', 
         meander=False, 
         extra_cmd=None, 
         extra_cmd_val=None,
         wait_first_datapoint=1,
         checkstepinterdelay=True,
         manualsetpoints=False,
         snapshot=True,
         do_plot=True):
    '''
    ----------------------------------------------------------------------------------------------------
    doNd: Generalised measurement function that is able to handle an arbitrary number of set parameters.
    ----------------------------------------------------------------------------------------------------
    
    Arguments:
    ----------

    param_set: QCoDeS instrument parameters used for setpoints of measurements. 
               The fastest axis is on the right, the slowest axis is on the left.
        - type: list
        - example: param_set = [set_param1, set_param2, ... etc]
    ..............................................................................................................
    spaces: Setpoints corresponding to the parameters in param_set.
            Functionality of spaces is changed by the manualsetpoints=True argument, see below
        - type: list of numpy arrays
        - example: space_set_param1 = np.linspace(0,10,11)
                   space_set_param2 = np.linspace(-3,3,7)
                   spaces = [space_set_param1, space_set_param2, ... etc]
    ..............................................................................................................
    settle_times: Waiting times in seconds for each datapoint after corresponding param_set have reached 
                  their destination values.
        - type: list of floats of length len(param_set)
        - example: settle_times = [settle_time1, settle_time2, ... etc]
    ..............................................................................................................
    param_meas: QCoDeS instrument parameters for which the values are recorded at each datapoint.
        - type: list
        - example: param_meas = [meas_param1, .. etc]
    ..............................................................................................................
    name: Name of the measurement.
        - type: string without special characters or spaces
        - example: name = 'Name of this measurement'
    ..............................................................................................................
    comment: Additional remarks, can contain special characters and spaces.
        - type: string
        - example:comment = 'More explanation'
    ..............................................................................................................
    meander: Use a meandering pattern for setpoints of the fastest and second to fastest measurement axis.
        - type: boolean
        - example: meander = False (default)
    ..............................................................................................................
    extra_cmd: Optional extra command that is executed before each param_meas is read out
        - type: list of class instances of functions of length len(param_meas)
        - example: extra_cmd = [cmd_meas_param1, cmd_meas_param2, ... etc]
    ..............................................................................................................
    extra_cmd_val: Optional extra value that of extra_cmd, it will be evaluated as extra_cmd(extra_cmd_val). 
                   If extra_cmd_val is not given, extra_cmd() will be run.
        - type: list of class instances of functions of length len(extra_cmd)
        - example: extra_cmd = [cmd_val_param1, cmd_val_param2, ... etc]
    ..............................................................................................................
    wait_first_datapoint: Additional seconds to wait before measuring the first datapoint of the measurement
        - type: float
        - example: wait_first_datapoint = 3
    ..............................................................................................................
    checkstepinterdelay: Checks if 'step' and 'inter_delay' have been set for all param_set. If not, 'inter_delay' 
                         is set to 5e-2 s and 'step' is set to the minumum spacing between setpoints of the 
                         corresponding param_set. Recommended to leave on for safety.
        - type: Boolean
        - example: checkstepinterdelay = True (default)
    ..............................................................................................................
    manualsetpoints: Modifies functionality of 'spaces'. Now, spaces should be supplied as an (n,m) sized array
                     containing all setpoints 'n' with param_set values 'm'.
        - type: Boolean
        - example: manualsetpoints = False (default)
    ..............................................................................................................
    snapshot: Controls taking a snapshot of the parameters of all connected instruments
        - type: boolean
        - example: snapshot = True (default) 
    ..............................................................................................................
    '''


    if manualsetpoints == False:
        if len(param_set) is not len(spaces):
            errstr = 'Error: number of param_set is ' + str(len(param_set)) + ', while number of spaces is ' + str(len(spaces)) + '.'
            sys.exit(errstr)
    if manualsetpoints == True:
        if isinstance(spaces,np.ndarray) == False:
            errstr = 'Error: spaces is of type '+ str(type(spaces)) +' not a numpy error as required when manualsetpoints=True.'    
            sys.exit(errstr)
        elif len(param_set) is not spaces.shape[1]:
            errstr = 'Error: number of param_set is ' + str(len(param_set)) + ', while dimension of spaces array is ' + str(spaces.shape[1]) + '.'
            sys.exit(errstr)
    
    if len(param_set) is not len(settle_times):
        errstr = 'Error: number of param_set is ' + str(len(param_set)) + ', while number of settle_times is ' + str(len(settle_times)) + '.' 
        sys.exit(errstr)
    # Register measid as global parameter
    global measid
    measid = None

    # Useless if statement, because why not        
    if __name__ != '__main__':
        #Create Event
        event = Event() # Create event shared by threads
        
        # Define p1 (run_measurement) and p2 (run_dbextractor) as two function to thread
        if param_set:
            p1 = Thread(target = run_measurement, args=(event, 
                                                        param_set, 
                                                        param_meas, 
                                                        spaces, 
                                                        settle_times, 
                                                        name, 
                                                        comment, 
                                                        meander, 
                                                        extra_cmd, 
                                                        extra_cmd_val, 
                                                        wait_first_datapoint,
                                                        checkstepinterdelay,
                                                        manualsetpoints,
                                                        snapshot))
        else:
            p1 = Thread(target = run_zerodim, args=(event, 
                                                    param_meas, 
                                                    name, 
                                                    comment,
                                                    wait_first_datapoint,
                                                    snapshot))
        # Set writeinterval db_extractor
        dbextractor_write_interval = 15 #sec
        p2 = Thread(target = run_dbextractor, args=(event,dbextractor_write_interval))
        
        # Kill main thread is subthreads are killed, not necessary here I think..
        #p1.daemon = True
        #p2.daemon = True
        
        #Starting the threads in a try except to catch kernel interrupts
        try:
            # Start the threads
            p1.start()
            p2.start()
            # If the child thread is still running
            while p1.is_alive():
                # Try to join the child thread back to parent for 0.5 seconds
                p1.join(0.5)
                p2.join(0.5)
        # When kernel interrupt is received (as keyboardinterrupt)
        except KeyboardInterrupt as e:
            # Set the alive attribute to false
            p1.alive = False
            p2.alive = False
            # Block until child thread is joined back to the parent
            p1.join()
            p2.join(5)
            # Exit with error code
            sys.exit(e)
    qctools.db_extraction.db_extractor(dbloc = qc.dataset.sqlite.database.get_DB_location(), 
                                       ids=[measid], 
                                       overwrite=True,
                                       newline_slowaxes=True,
                                       no_folders=False,
                                       suppress_output=True,
                                       useopendbconnection = True)
    if len(param_set) > 2 or not do_plot:
        print('QCoDeS currently does not support plotting of higher dimensional data, plotting skipped.')
    else:
        plot_by_id(measid)
    #sys.exit(0)
    return measid