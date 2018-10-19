#!/usr/local/lcls/package/python/current/bin/python
# Written by Zimmer, edited by Ahmed, refactored by Lisa

import sys
import time

from epics import PV

from numpy import (polyfit, poly1d, polyval, corrcoef, std, mean, concatenate,
                   empty, append, nan, max as np_max, min as np_min, zeros,
                   isnan, linalg, abs, fft, argsort, interp)

from PyQt4.QtCore import *
from PyQt4.QtGui import *
from subprocess import CalledProcessError, check_output

from logbook import *
from rtbsa_ui import Ui_RTBSA
from Constants import *

PEAK_CURRENT_LIMIT = 12000


# noinspection PyArgumentList,PyCompatibility
class RTBSA(QMainWindow):

    def __init__(self, parent=None):
        QMainWindow.__init__(self, parent)
        self.help_menu = self.menuBar().addMenu("&Help")
        self.file_menu = self.menuBar().addMenu("&File")
        self.status_text = QLabel()
        self.plot = pg.PlotWidget(alpha=0.75)
        self.ui = Ui_RTBSA()
        self.ui.setupUi(self)
        self.setWindowTitle('Real Time BSA')
        self.loadStyleSheet()
        self.setUpGraph()

        self.bsapvs = ['GDET:FEE1:241:ENRC', 'GDET:FEE1:242:ENRC',
                       'GDET:FEE1:361:ENRC', 'GDET:FEE1:362:ENRC']

        self.populateBSAPVs()
        self.connectGuiFunctions()

        # Initial number of points
        self.numpoints = 2800

        # Initial number of standard deviations
        self.stdDevstoKeep = 3.0

        # 20ms polling time
        self.updateTime = 50

        # Set initial polynomial fit to 2
        self.fitOrder = 2

        self.disableInputs()
        self.abort = True

        # Used to update plot
        self.timer = QTimer(self)

        self.ratePV = PV('IOC:IN20:EV01:RG01_ACTRATE')

        # IOC:IN20:EV01:RG01_ACTRATE returns one of 7 states, 0 through 6, where
        # 0 is NULL (unclear what that means, but doesn't sound good), 1 is 0Hz,
        # 2 is 1Hz, 3 is 10Hz, 4 is 30Hz, 5 is 60Hz, and 6 is 120Hz
        self.rateDict = {0: None, 1: 0.0, 2: 1.0, 3: 10.0, 4: 30.0, 5: 60.0,
                         6: 120.0}

        self.menuBar().setStyleSheet(
            'QWidget{background-color:grey;color:purple}')
        self.create_menu()
        self.create_status_bar()

        # The PV names
        self.devices = {"A": "", "B": ""}

        self.pvObjects = {"A": None, "B": None}

        # The raw, unsynchronized, unfiltered buffers
        self.rawBuffers = {"A": [], "B": []}

        # The times when each buffer finished its last data acquisition
        self.timeStamps = {"A": None, "B": None}

        self.synchronizedBuffers = {"A": [], "B": []}

        # Versions of data buffers A and B that are filtered by standard
        # deviation. Didn't want to edit those buffers directly so that we could
        # unfliter or refilter with a different number more efficiently
        self.filteredBuffers = {"A": [], "B": []}

        # Text objects that appear on the plot
        self.text = {"avg": None, "std": None, "slope": None, "corr": None}

        # All things plot related!
        self.plotAttributes = {"curve": None, "xData": None, "fit": None,
                               "parab": None, "frequencies": None}

    def disableInputs(self):
        self.ui.fitedit.setDisabled(True)
        self.ui.enter1.setDisabled(True)
        self.ui.enter2.setDisabled(True)
        self.ui.label.setDisabled(True)
        self.ui.listWidget.setDisabled(True)
        self.ui.listWidget_2.setDisabled(True)
        self.statusBar().showMessage('Hi there!  I missed you!')
        self.ui.parab_cb.setChecked(False)

    def populateBSAPVs(self):
        # Generate list of BSA PVS
        try:
            BSAPVs = check_output(['eget', '-ts', 'ds', '-a',
                                   'tag=LCLS.BSA.rootnames']).splitlines()[1:-1]
            self.bsapvs.extend(BSAPVs)

        # Backup for eget error
        except CalledProcessError:
            print("Unable to pull most recent PV list")
            # bsaPVs is pulled from the Constants file
            self.bsapvs.extend(bsaPVs)

        for pv in self.bsapvs:
            self.ui.listWidget.addItem(pv)
            self.ui.listWidget_2.addItem(pv)

    def connectGuiFunctions(self):
        # enter 1 is the text input box for device A, and 2 is for B
        QObject.connect(self.ui.enter1, SIGNAL("textChanged(const QString&)"),
                        self.searchA)
        QObject.connect(self.ui.enter2, SIGNAL("textChanged(const QString&)"),
                        self.searchB)

        # Changes the text in the input box to match the selection from the list
        self.ui.listWidget.itemClicked.connect(self.setEnterA)
        self.ui.listWidget_2.itemClicked.connect(self.setEnterB)

        # Dropdown menu for device A (add common BSA PV's and make bunch length
        # the default selection)
        self.ui.common1.addItems(commonlist)
        self.ui.common1.setCurrentIndex(commonlist.index("BLEN:LI24:886:BIMAX"))
        self.ui.common1.activated.connect(self.inputActivated)

        # Dropdown menu for device B
        self.ui.common2.addItems(commonlist)
        self.ui.common2.activated.connect(self.inputActivated)

        # All the checkboxes in the Settings section
        self.ui.AvsT_cb.clicked.connect(self.AvsTClick)
        self.ui.AvsB.clicked.connect(self.AvsBClick)
        self.ui.AFFT.clicked.connect(self.AFFTClick)
        self.ui.avg_cb.clicked.connect(self.avg_click)
        self.ui.std_cb.clicked.connect(self.std_click)
        self.ui.corr_cb.clicked.connect(self.corr_click)
        self.ui.parab_cb.clicked.connect(self.parab_click)
        self.ui.line_cb.clicked.connect(self.line_click)

        self.ui.grid_cb.clicked.connect(self.showGrid)

        # All the buttons in the Controls section
        self.ui.draw_button.clicked.connect(self.onDraw)
        self.ui.stop_button.clicked.connect(self.stop)
        self.ui.log_button.clicked.connect(self.logbook)
        self.ui.mcclog_button.clicked.connect(self.MCCLog)

        # fitedit is the text input box for "Order"
        self.ui.fitedit.returnPressed.connect(self.fitOrderActivated)

        # The radio buttons that enable the dropdown menus
        self.ui.common1_rb.clicked.connect(self.common_1_click)
        self.ui.common2_rb.clicked.connect(self.common_2_click)

        # The radio buttons that enable the search bars
        self.ui.enter1_rb.clicked.connect(self.enter_1_click)
        self.ui.enter2_rb.clicked.connect(self.enter_2_click)

        # Pressing enter in the text input boxes for points and std dev triggers
        # updating the plot
        self.ui.points.returnPressed.connect(self.points_entered)
        self.ui.numStdDevs.returnPressed.connect(self.stdDevEntered)

    def showGrid(self):
        self.plot.showGrid(self.ui.grid_cb.isChecked(),
                           self.ui.grid_cb.isChecked())

    def setUpGraph(self):
        layout = QGridLayout()
        self.ui.widget.setLayout(layout)
        layout.addWidget(self.plot, 0, 0)
        self.plot.showGrid(1, 1)

    def loadStyleSheet(self):
        cssFile = "style.css"
        try:
            with open(cssFile, "r") as f:
                self.setStyleSheet(f.read())
        except IOError:
            print("Error loading style sheet")
            pass

    def create_status_bar(self):
        palette = QPalette()
        palette.setColor(palette.Foreground, Qt.magenta)
        self.statusBar().addWidget(self.status_text, 1)
        self.statusBar().setPalette(palette)

    # Effectively an autocomplete
    def search(self, enter, widget):
        widget.clear()
        query = str(enter.text())
        for pv in self.bsapvs:
            if query.lower() in pv.lower():
                widget.addItem(pv)

    def searchA(self):
        self.search(self.ui.enter1, self.ui.listWidget)

    def searchB(self):
        self.search(self.ui.enter2, self.ui.listWidget_2)

    def setEnter(self, widget, enter, search, enter_rb):
        selection = widget.currentItem()
        enter.textChanged.disconnect()
        enter.setText(selection.text())
        QObject.connect(enter, SIGNAL("textChanged(const QString&)"), search)
        if not self.abort and enter_rb.isChecked():
            self.stop()
            self.onDraw()

    def setEnterA(self):
        self.setEnter(self.ui.listWidget, self.ui.enter1, self.searchA,
                      self.ui.enter1_rb)

    def setEnterB(self):
        self.setEnter(self.ui.listWidget_2, self.ui.enter2, self.searchB,
                      self.ui.enter2_rb)

    def correctInput(self, errorMessage, acceptableTxt, textBox):
        self.statusBar().showMessage(errorMessage, 6000)
        textBox.setText(acceptableTxt)

    def correctNumpoints(self, errorMessage, acceptableValue):
        self.correctInput(errorMessage, str(acceptableValue), self.ui.points)
        self.numpoints = acceptableValue

    def correctStdDevs(self, errorMessage, acceptableValue):
        self.correctInput(errorMessage, str(acceptableValue),
                          self.ui.numStdDevs)
        self.stdDevstoKeep = acceptableValue

    def stdDevEntered(self):
        try:
            self.stdDevstoKeep = float(self.ui.numStdDevs.text())
        except ValueError:
            self.correctStdDevs('Enter a float > 0', 3.0)
            return

        # Is there a way to combine an except and an if?
        if self.stdDevstoKeep <= 0:
            self.correctStdDevs('Enter a float > 0', 3.0)
            return

    def points_entered(self):
        try:
            self.numpoints = int(self.ui.points.text())
        except ValueError:
            self.correctNumpoints('Enter an integer, 1 to 2800', 120)
            return

        if self.numpoints > 2800:
            self.correctNumpoints('Max # points is 2800', 2800)
            return

        if self.numpoints < 1:
            self.correctNumpoints('Min # points is 1', 1)
            return

        self.reinitialize_plot()

    ############################################################################
    # Where the magic happens (well, where it starts to happen). This
    # initializes the BSA plotting and then starts a timer to update the plot.
    ############################################################################
    def onDraw(self):
        plotTypeIsValid = (self.ui.AvsT_cb.isChecked()
                           or self.ui.AvsB.isChecked()
                           or self.ui.AFFT.isChecked())

        if not plotTypeIsValid:
            self.statusBar().showMessage('Pick a Plot Type (PV vs. time '
                                         'or B vs A)', 10000)
            return

        self.ui.draw_button.setDisabled(True)
        self.abort = False

        self.cleanPlot()
        self.pvObjects["A"], self.pvObjects["B"] = None, None

        # Plot history buffer for one PV
        if self.ui.AvsT_cb.isChecked():
            if self.populateDevices(self.ui.common1_rb, self.ui.common1,
                                    self.ui.enter1_rb, self.ui.enter1, "A"):
                self.genPlotAndSetTimer(self.genTimePlotA,
                                        self.updateTimePlotA)

        # Plot for 2 PVs
        elif self.ui.AvsB.isChecked():
            if self.updateValsFromInput():
                self.genPlotAndSetTimer(self.genPlotAB,
                                        self.updatePlotAB)

        # Plot power spectrum
        else:
            if self.populateDevices(self.ui.common1_rb, self.ui.common1,
                                    self.ui.enter1_rb, self.ui.enter1, "A"):
                self.genPlotAndSetTimer(self.InitializeFFTPlot,
                                        self.updatePlotFFT)

    def populateDevices(self, common_rb, common, enter_rb, enter, device):

        if common_rb.isChecked():
            self.devices[device] = str(common.currentText())

        elif enter_rb.isChecked():
            pv = str(enter.text()).strip()

            # Checks that it's non empty and that it's a BSA pv
            if pv and pv in self.bsapvs:
                self.devices[device] = pv
            else:
                self.statusBar().showMessage('Device ' + device
                                             + ' invalid. Aborting.', 10000)
                self.ui.draw_button.setEnabled(True)
                return False

        return True

    def updateValsFromInput(self):

        if not self.populateDevices(self.ui.common1_rb, self.ui.common1,
                                    self.ui.enter1_rb, self.ui.enter1, "A"):
            return False

        if not self.populateDevices(self.ui.common2_rb, self.ui.common2,
                                    self.ui.enter2_rb, self.ui.enter2, "B"):
            return False

        self.statusBar().showMessage('Initializing/Syncing (be patient, '
                                     + 'may take 5 seconds)...')

        # Initial population of our buffers using the HSTBR PV's in our
        # callback functions
        self.clearAndUpdateCallbacks("HSTBR", True)

        while ((not self.timeStamps["A"] or not self.timeStamps["B"])
               and not self.abort):
            QApplication.processEvents()

        self.adjustVals()

        # Switch to BR PVs to avoid pulling an entire history buffer on every
        # update.
        self.clearAndUpdateCallbacks("BR")

        return True

    def clearAndUpdateCallbacks(self, suffix, resetTime=False):
        self.clearAndUpdateCallback("A", suffix, self.callbackA,
                                    self.devices["A"], resetTime)
        self.clearAndUpdateCallback("B", suffix, self.callbackB,
                                    self.devices["B"], resetTime)

    # noinspection PyTypeChecker
    def clearAndUpdateCallback(self, device, suffix, callback, pvName,
                               resetTime=False):
        self.clearPV(device)

        # Without the time parameter, we wouldn't get the timestamp
        self.pvObjects[device] = PV(pvName + suffix, form='time')

        if resetTime:
            self.timeStamps[device] = None

        self.pvObjects[device].add_callback(callback)

    # Callback function for Device A
    # noinspection PyUnusedLocal
    def callbackA(self, pvname=None, value=None, timestamp=None, **kw):
        self.updateTimeAndBuffer("A", pvname, timestamp, value)

    # Callback function for Device B
    # noinspection PyUnusedLocal
    def callbackB(self, pvname=None, value=None, timestamp=None, **kw):
        self.updateTimeAndBuffer("B", pvname, timestamp, value)

    ############################################################################
    # This is where the data is actually acquired and saved to the buffers.
    # Callbacks are effectively listeners that listen for change, so we
    # basically put a callback on the PVs of interest (devices A and/or B) so
    # that every time the value of that PV changes, we get that new value and
    # append it to our raw data buffer for that device.
    # Initialization of the buffer is slightly different in that the listener is
    # put on the history buffer of that PV (denoted by the HSTBR suffix), so
    # that we just immediately write the previous 2800 points to our raw buffer
    ############################################################################
    def updateTimeAndBuffer(self, device, pvname, timestamp, value):
        if pvname[-5:] == 'HSTBR':
            self.timeStamps[device] = timestamp

            # value is the buffer because we're monitoring the HSTBR PV
            self.rawBuffers[device] = value
        else:
            # return
            if not self.timeStamps[device]:
                return

            elapsed_time = (timestamp - self.timeStamps[device])
            elapsed_points = int(round(elapsed_time * self.updateRate()))

            if elapsed_points <= 0:
                return

            self.timeStamps[device] = timestamp

            nanArray = empty(elapsed_points - 1)
            nanArray[:] = nan

            truncatedData = self.rawBuffers[device][elapsed_points:]

            baseArray = concatenate([truncatedData, nanArray])

            self.rawBuffers[device] = append(baseArray, value)

    def clearPV(self, device):
        pv = self.pvObjects[device]
        if pv:
            pv.clear_callbacks()
            pv.disconnect()

    def adjustVals(self):
        self.updateRate()

        numBadShots = self.setValSynced()
        blength = 2800 - numBadShots

        # Make sure the buffer size doesn't exceed the desired number of points
        if self.numpoints < blength:
            self.synchronizedBuffers["A"] = self.synchronizedBuffers["A"][
                                            blength - self.numpoints:blength]
            self.synchronizedBuffers["B"] = self.synchronizedBuffers["B"][
                                            blength - self.numpoints:blength]

    # A spin loop that waits until the beam rate is at least 1Hz
    def updateRate(self):

        start_time = time.time()
        gotStuckAndNeedToUpdateMessage = False

        # self.rate is a PV, such that .value is shorthand for .getval
        while self.ratePV.value < 2:
            # noinspection PyArgumentList
            QApplication.processEvents()

            if time.time() - start_time > 1:
                gotStuckAndNeedToUpdateMessage = True
                self.statusBar().showMessage("Waiting for beam rate to be at "
                                             "least 1Hz...")

        if gotStuckAndNeedToUpdateMessage:
            self.statusBar().showMessage("Beam rate at allowed value")

        return self.rateDict[self.ratePV.value]

    ############################################################################
    # Time 1 is when Device A started acquiring data, and Time 2 is when Device
    # B started acquiring data. Since they're not guaranteed to start
    # acquisition at the same time, one data buffer might be ahead of the other,
    # meaning that the intersection of the two buffers would not include the
    # first n elements of one and the last n elements of the other. See the
    # diagram below, where the dotted line represents the time axis (one buffer
    # is contained  by square brackets [], the other by curly braces {}, and the
    # times where each starts  and ends is indicated right underneath).
    #
    #
    #          [           {                            ]           }
    # <----------------------------------------------------------------------> t
    #       t1_start    t2_start                     t1_end      t2_end
    #
    #
    # Note that both buffers are of the same size (self.numpoints) so that:
    # (t1_end - t1_start) = (t2_end - t2_start)
    #
    # From the diagram, we see that only the time between t2_start and t1_end
    # contains data from both buffers (t1_start to t2_start only contains data
    # from buffer 1, and t1_end to t2_end only contains data from buffer 2).
    # Using that, we can chop the beginning of buffer 1 and the end of buffer 2
    # so that we're only left with the overlapping region.
    #
    # In order to figure out how many points we need to chop from each buffer
    # (it's the same number for both since they're both the same size), we
    # multiply the time delta by the beam rate (yay dimensional analysis!):
    # seconds * (shots/second) = (number of shots)
    ############################################################################
    def setValSynced(self):
        numBadShots = int(round((self.timeStamps["B"] - self.timeStamps["A"])
                                * self.updateRate()))

        startA, endA = self.getIndices(numBadShots, 1)
        startB, endB = self.getIndices(numBadShots, -1)

        self.synchronizedBuffers["A"] = self.rawBuffers["A"][startA:endA]
        self.synchronizedBuffers["B"] = self.rawBuffers["B"][startB:endB]

        return abs(numBadShots)

    # The @ is an annotation symbol that tells the interpreter something about
    # the thing it's annotating. In this case, it's telling us that getIndices
    # is a static method, meaning that it doesn't do anything with class
    # variables (there's no need for a "self" parameter)
    @staticmethod
    def getIndices(numBadShots, mult):
        # Gets opposite indices depending on which time is greater (and [0:2800]
        # if they're equal)
        return max(0, mult * numBadShots), min(2800, 2800 + mult * numBadShots)

    def genPlotAndSetTimer(self, genPlot, updateMethod):
        if self.abort:
            return

        try:
            genPlot()
        except UnboundLocalError:
            self.statusBar().showMessage('No Data, Aborting Plotting Algorithm',
                                         10000)
            return

        self.timer = QTimer(self)

        # Run updateMethod every updatetime milliseconds
        self.timer.singleShot(self.updateTime, updateMethod)

        self.statusBar().showMessage('Running')

    # noinspection PyTypeChecker
    def genTimePlotA(self):
        newData = self.initializeData()

        if not newData:
            self.statusBar().showMessage('Invalid PV? Unable to get data.'
                                         + ' Aborting.', 10000)
            self.ui.draw_button.setEnabled(True)
            return

        data = newData[-self.numpoints:]

        self.plotAttributes["curve"] = pg.PlotCurveItem(data, pen=1)
        self.plot.addItem(self.plotAttributes["curve"])

        self.plotAttributes["xData"] = range(self.numpoints)

        self.plotFit(self.plotAttributes["xData"], data, self.devices["A"])

    ############################################################################
    # This is the main plotting function for "Plot A vs Time" that gets called
    # every self.updateTime seconds
    # noinspection PyTypeChecker
    ############################################################################
    def updateTimePlotA(self):

        QApplication.processEvents()

        if self.abort:
            return

        xData, yData = self.filterTimePlotBuffer()

        if yData:
            self.plotAttributes["curve"].setData(yData)
            if self.ui.autoscale_cb.isChecked():
                mx = max(yData)
                mn = min(yData)
                if mx - mn > .00001:
                    self.plot.setYRange(mn, mx)
                    self.plot.setXRange(0, len(yData))

            if self.ui.avg_cb.isChecked():
                self.setPosAndText(self.text["avg"], mean(yData), 0, min(yData),
                                   'AVG: ')

            if self.ui.std_cb.isChecked():
                self.setPosAndText(self.text["std"], std(yData),
                                   self.numpoints / 4,
                                   min(yData), 'STD: ')

            if self.ui.corr_cb.isChecked():
                self.text["corr"].setText('')

            if self.ui.line_cb.isChecked():
                self.text["slope"].setPos(self.numpoints / 2, min(yData))
                self.getLinearFit(xData, yData, True)

            elif self.ui.parab_cb.isChecked():
                self.text["slope"].setPos(self.numpoints / 2, min(yData))
                self.getPolynomialFit(xData, yData, True)

        self.timer.singleShot(self.updateTime, self.updateTimePlotA)

    def filterTimePlotBuffer(self):
        choppedBuffer = self.rawBuffers["A"][-self.numpoints:]

        xData, yData = self.filterBuffers(choppedBuffer,
                                          lambda x: not isnan(x),
                                          self.plotAttributes["xData"],
                                          choppedBuffer)

        if self.devices["A"] == "BLEN:LI24:886:BIMAX":
            xData, yData = self.filterBuffers(yData,
                                              lambda x: x < PEAK_CURRENT_LIMIT,
                                              xData, yData)

        if self.ui.filterByStdDevs.isChecked():
            stdDevFilterFunc = self.StdDevFilterFunc(mean(yData), std(yData))
            xData, yData = self.filterBuffers(yData, stdDevFilterFunc, xData,
                                              yData)
        return xData, yData

    @staticmethod
    def setPosAndText(attribute, value, posValX, posValY, textVal):
        value = "{:.3}".format(value)
        attribute.setPos(posValX, posValY)
        attribute.setText(textVal + str(value))

    def getLinearFit(self, xData, yData, updateExistingPlot):
        try:
            # noinspection PyTupleAssignmentBalance
            m, b = polyfit(xData, yData, 1)
            fitData = polyval([m, b], xData)

            self.text["slope"].setText('Slope: ' + str("{:.3e}".format(m)))

            if updateExistingPlot:
                self.plotAttributes["fit"].setData(xData, fitData)
            else:
                # noinspection PyTypeChecker
                self.plotAttributes["fit"] = pg.PlotCurveItem(xData, fitData,
                                                              'g-', linewidth=1)
        except:
            print("Error getting linear fit")
            pass

    def getPolynomialFit(self, xData, yData, updateExistingPlot):
        try:
            co = polyfit(xData, yData, self.fitOrder)
            pol = poly1d(co)
            xDataSorted = sorted(xData)
            fit = pol(xDataSorted)

            if updateExistingPlot:
                self.plotAttributes["parab"].setData(xDataSorted, fit)
            else:
                # noinspection PyTypeChecker
                self.plotAttributes["parab"] = pg.PlotCurveItem(xDataSorted,
                                                                fit, pen=3,
                                                                size=2)

            if self.fitOrder == 2:
                self.text["slope"].setText('Peak: ' + str(-co[1] / (2 * co[0])))

            elif self.fitOrder == 3:
                self.text["slope"].setText(str("{:.2e}".format(co[0])) + 'x^3'
                                           + str("+{:.2e}".format(co[1]))
                                           + 'x^2'
                                           + str("+{:.2e}".format(co[2])) + 'x'
                                           + str("+{:.2e}".format(co[3])))

        except linalg.linalg.LinAlgError:
            print("Linear algebra error getting curve fit")
            pass
        except:
            self.text["slope"].setText('Fit failed')
            pass

    def genPlotAB(self):
        if self.ui.filterByStdDevs.isChecked():
            self.plotCurveAndFit(self.filteredBuffers["A"],
                                 self.filteredBuffers["B"])
        else:
            self.plotCurveAndFit(self.synchronizedBuffers["A"],
                                 self.synchronizedBuffers["B"])

    def plotCurveAndFit(self, xData, yData):
        # noinspection PyTypeChecker
        self.plotAttributes["curve"] = pg.ScatterPlotItem(xData, yData, pen=1,
                                                          symbol='x', size=5)
        self.plot.addItem(self.plotAttributes["curve"])
        self.plotFit(xData, yData,
                     self.devices["B"] + ' vs. ' + self.devices["A"])

    def plotFit(self, xData, yData, title):
        self.plot.addItem(self.plotAttributes["curve"])
        self.plot.setTitle(title)

        # Fit line
        if self.ui.line_cb.isChecked():
            self.getLinearFit(xData, yData, False)
            self.plot.addItem(self.plotAttributes["fit"])

        # Fit polynomial
        elif self.ui.parab_cb.isChecked():
            self.ui.fitedit.setDisabled(False)
            self.getPolynomialFit(xData, yData, False)
            self.plot.addItem(self.plotAttributes["parab"])

    ############################################################################
    # This is the main plotting function for "Plot B vs A" that gets called
    # every self.updateTime seconds
    ############################################################################
    def updatePlotAB(self):
        if self.abort:
            return

        QApplication.processEvents()

        self.adjustVals()
        self.filterNans()
        self.filterPeakCurrent()

        if self.ui.filterByStdDevs.isChecked():
            self.filterStdDev()
            self.updateLabelsAndFit(self.filteredBuffers["A"],
                                    self.filteredBuffers["B"])
        else:
            self.updateLabelsAndFit(self.synchronizedBuffers["A"],
                                    self.synchronizedBuffers["B"])

        self.timer.singleShot(self.updateTime, self.updatePlotAB)

    def filterNans(self):
        def filterFunc(x): return ~isnan(x)

        self.filterData(self.synchronizedBuffers["A"], filterFunc, True)
        self.filterData(self.synchronizedBuffers["B"], filterFunc, True)

    # Need to filter out errant indices from both buffers to keep them
    # synchronized
    def filterData(self, dataBuffer, filterFunc, changeOriginal):
        bufferA, bufferB = self.filterBuffers(dataBuffer, filterFunc,
                                              self.synchronizedBuffers["A"],
                                              self.synchronizedBuffers["B"])

        if changeOriginal:
            self.synchronizedBuffers["A"] = bufferA
            self.synchronizedBuffers["B"] = bufferB
        else:
            self.filteredBuffers["A"] = bufferA
            self.filteredBuffers["B"] = bufferB

    @staticmethod
    def filterBuffers(bufferToFilter, filterFunc, xData, yData):
        mask = filterFunc(bufferToFilter)
        return xData[mask], yData[mask]

    # This PV gets insane values, apparently
    def filterPeakCurrent(self):
        def filterFunc(x): return x < PEAK_CURRENT_LIMIT

        if self.devices["A"] == "BLEN:LI24:886:BIMAX":
            self.filterData(self.synchronizedBuffers["A"], filterFunc, True)
        if self.devices["B"] == "BLEN:LI24:886:BIMAX":
            self.filterData(self.synchronizedBuffers["B"], filterFunc, True)

    def filterStdDev(self):

        bufferA = self.synchronizedBuffers["A"]
        bufferB = self.synchronizedBuffers["B"]

        self.filterData(bufferA, self.StdDevFilterFunc(mean(bufferA),
                                                       std(bufferA)), False)

        self.filterData(bufferB, self.StdDevFilterFunc(mean(bufferB),
                                                       std(bufferB)), False)

    def StdDevFilterFunc(self, average, stdDev):
        return lambda x: abs(x - average) < self.stdDevstoKeep * stdDev

    # noinspection PyTypeChecker
    def updateLabelsAndFit(self, bufferA, bufferB):
        self.plotAttributes["curve"].setData(bufferA, bufferB)

        self.setPlotRanges(bufferA, bufferB)

        # Logic to determine positions of labels when not running autoscale
        if self.ui.avg_cb.isChecked():
            self.setPosAndText(self.text["avg"], mean(bufferB), min(bufferA),
                               min(bufferB), 'AVG: ')

        if self.ui.std_cb.isChecked():
            val1Min = min(bufferA)
            xPos = (val1Min + (val1Min + max(bufferA)) / 2) / 2

            self.setPosAndText(self.text["std"], std(bufferB), xPos,
                               min(bufferB),
                               'STD: ')

        if self.ui.corr_cb.isChecked():
            correlation = corrcoef(bufferA, bufferB)
            self.setPosAndText(self.text["corr"], correlation.item(1),
                               min(bufferA), max(bufferB),
                               "Corr. Coefficient: ")

        if self.ui.line_cb.isChecked():
            self.text["slope"].setPos((min(bufferA) + max(bufferA)) / 2,
                                      min(bufferB))
            self.getLinearFit(bufferA, bufferB, True)

        elif self.ui.parab_cb.isChecked():
            self.text["slope"].setPos((min(bufferA) + max(bufferA)) / 2,
                                      min(bufferB))
            self.getPolynomialFit(bufferA, bufferB, True)

    def setPlotRanges(self, bufferA, bufferB):
        mx = np_max(bufferB)
        mn = np_min(bufferB)

        if mn != mx:
            self.plot.setYRange(mn, mx)

        mx = np_max(bufferA)
        mn = np_min(bufferA)

        if mn != mx:
            self.plot.setXRange(mn, mx)

    def InitializeFFTPlot(self):
        self.genPlotFFT(self.initializeData(), False)

    # TODO I have no idea what's happening here
    def genPlotFFT(self, newdata, updateExistingPlot):

        if not newdata.size:
            return None

        newdata = newdata[-self.numpoints:]

        nans, x = isnan(newdata), lambda z: z.nonzero()[0]
        # interpolate nans
        newdata[nans] = interp(x(nans), x(~nans), newdata[~nans])
        # remove DC component
        newdata = newdata - mean(newdata)

        newdata = concatenate([newdata, zeros(self.numpoints * 2)])

        ps = abs(fft.fft(newdata)) / newdata.size

        frequencies = fft.fftfreq(newdata.size, 1.0 / self.updateRate())
        keep = (frequencies >= 0)
        ps = ps[keep]
        frequencies = frequencies[keep]
        idx = argsort(frequencies)

        if updateExistingPlot:
            self.plotAttributes["curve"].setData(x=frequencies[idx], y=ps[idx])
        else:
            # noinspection PyTypeChecker
            self.plotAttributes["curve"] = pg.PlotCurveItem(x=frequencies[idx],
                                                            y=ps[idx], pen=1)

        self.plot.addItem(self.plotAttributes["curve"])
        self.plot.setTitle(self.devices["A"])
        self.plotAttributes["frequencies"] = frequencies

        return ps

    # noinspection PyTypeChecker
    def cleanPlot(self):
        self.plot.clear()

        self.text["avg"] = pg.TextItem('', color=(200, 200, 250), anchor=(0, 1))
        self.text["std"] = pg.TextItem('', color=(200, 200, 250), anchor=(0, 1))
        self.text["slope"] = pg.TextItem('', color=(200, 200, 250),
                                         anchor=(0, 1))
        self.text["corr"] = pg.TextItem('', color=(200, 200, 250),
                                        anchor=(0, 1))

        plotLabels = [self.text["avg"], self.text["std"], self.text["slope"],
                      self.text["corr"]]

        for plotLabel in plotLabels:
            self.plot.addItem(plotLabel)

    def initializeData(self):
        self.statusBar().showMessage('Initializing...')

        if self.ui.common1_rb.isChecked():
            self.devices["A"] = str(self.ui.common1.currentText())

        elif self.ui.enter1_rb.isChecked():
            pv = str(self.ui.enter1.text()).strip()
            if pv and pv in self.bsapvs:
                self.devices["A"] = pv
            else:
                return None
        else:
            return None

        # Initializing our data by putting a callback on the history buffer PV
        self.clearAndUpdateCallback("A", "HSTBR", self.callbackA,
                                    self.devices["A"], True)

        while (not self.timeStamps["A"]) and not self.abort:
            QApplication.processEvents()

        # Removing that callback and manually appending new values to our local
        # data buffer using the usual PV
        # TODO ask Ahmed what the BR is for
        self.clearAndUpdateCallback("A", "BR", self.callbackA,
                                    self.devices["A"])

        # This was populated in the callback function
        return self.rawBuffers["A"]

    ############################################################################
    # This is the main plotting function for "Plot A FFT" that gets called
    # every self.updateTime seconds
    ############################################################################
    def updatePlotFFT(self):
        QApplication.processEvents()

        if self.abort:
            return

        ps = self.genPlotFFT(self.rawBuffers["A"], True)

        if self.ui.autoscale_cb.isChecked():
            mx = max(ps)
            mn = min(ps)
            if mx - mn > .00001:
                frequencies = self.plotAttributes["frequencies"]
                self.plot.setYRange(mn, mx)
                # noinspection PyTypeChecker
                self.plot.setXRange(min(frequencies), max(frequencies))

        self.timer.singleShot(self.updateTime, self.updatePlotFFT)

    def AvsTClick(self):
        if not self.ui.AvsT_cb.isChecked():
            pass
        else:
            self.ui.AvsB.setChecked(False)
            self.ui.AFFT.setChecked(False)
            self.AvsBClick()

    def AvsBClick(self):
        if not self.ui.AvsB.isChecked():
            self.ui.groupBox_2.setDisabled(True)
            self.ui.enter2_rb.setChecked(False)
            self.ui.enter2_rb.setDisabled(True)
            self.ui.enter2.setDisabled(True)
            self.ui.common2.setDisabled(True)
            self.ui.common2_rb.setChecked(False)
            self.ui.common2_rb.setDisabled(True)
        else:
            self.ui.AvsT_cb.setChecked(False)
            self.ui.AFFT.setChecked(False)
            self.AvsTClick()
            self.ui.groupBox_2.setDisabled(False)
            self.ui.listWidget_2.setDisabled(True)
            self.ui.enter2_rb.setDisabled(False)
            self.ui.enter2.setDisabled(True)
            self.ui.common2_rb.setDisabled(False)
            self.ui.common2_rb.setChecked(True)
            self.ui.common2.setDisabled(False)
        self.stop()

    def AFFTClick(self):
        if not self.ui.AFFT.isChecked():
            pass
        else:
            self.ui.AvsB.setChecked(False)
            self.ui.AvsT_cb.setChecked(False)
            self.AvsBClick()

    def avg_click(self):
        if not self.ui.avg_cb.isChecked():
            self.text["avg"].setText('')

    def std_click(self):
        if not self.ui.std_cb.isChecked():
            self.text["std"].setText('')

    def corr_click(self):
        if not self.ui.corr_cb.isChecked():
            self.text["corr"].setText('')

    def enter_1_click(self):
        if self.ui.enter1_rb.isChecked():
            self.ui.enter1.setDisabled(False)
            self.ui.listWidget.setDisabled(False)
            self.ui.common1_rb.setChecked(False)
            self.ui.common1.setDisabled(True)
        else:
            self.ui.enter1.setDisabled(True)

    def enter_2_click(self):
        if self.ui.enter2_rb.isChecked():
            self.ui.enter2.setDisabled(False)
            self.ui.listWidget_2.setDisabled(False)
            self.ui.common2_rb.setChecked(False)
            self.ui.common2.setDisabled(True)
        else:
            self.ui.enter2.setDisabled(True)

    def common_1_click(self):
        if self.ui.common1_rb.isChecked():
            self.ui.common1.setEnabled(True)
            self.ui.enter1_rb.setChecked(False)
            self.ui.enter1.setDisabled(True)
            self.ui.listWidget.setDisabled(True)
        else:
            self.ui.common1.setEnabled(False)
        self.inputActivated()

    def inputActivated(self):
        if not self.abort:
            self.stop()
            self.timer.singleShot(self.updateTime, self.onDraw)

    def common_2_click(self):
        if self.ui.common2_rb.isChecked():
            self.ui.common2.setEnabled(True)
            self.ui.enter2_rb.setChecked(False)
            self.ui.enter2.setDisabled(True)
            self.ui.listWidget_2.setDisabled(True)
        else:
            self.ui.common2.setEnabled(False)
        self.inputActivated()

    def line_click(self):
        self.ui.parab_cb.setChecked(False)
        self.ui.fitedit.setDisabled(True)
        self.ui.label.setDisabled(True)
        self.reinitialize_plot()

    def fitOrderActivated(self):
        try:
            self.fitOrder = int(self.ui.fitedit.text())
        except ValueError:
            self.statusBar().showMessage('Enter an integer, 1-10', 6000)
            return

        if self.fitOrder > 10 or self.fitOrder < 1:
            self.statusBar().showMessage('Really?  That is going to be useful'
                                         + ' to you?  The (already ridiculous)'
                                         + ' range is 1-10.  Hope you win a '
                                         + 'nobel prize jackass.', 6000)
            self.ui.fitedit.setText('2')
            self.fitOrder = 2

        if self.fitOrder != 2:
            try:
                self.text["slope"].setText('')
            except AttributeError:
                pass

    def parab_click(self):
        self.ui.line_cb.setChecked(False)

        if not self.ui.parab_cb.isChecked():
            self.ui.fitedit.setDisabled(True)
            self.ui.label.setDisabled(True)
        else:
            self.ui.fitedit.setEnabled(True)
            self.ui.label.setEnabled(True)
        self.reinitialize_plot()

    # This is a mess, but it works (used if user changes number points,
    # fit type etc.)
    def reinitialize_plot(self):
        self.cleanPlot()

        try:
            # Setup for single PV plotting
            if self.ui.AvsT_cb.isChecked():
                self.genTimePlotA()

            elif self.ui.AvsB.isChecked():
                self.genPlotAB()
            else:
                self.genPlotFFT(self.synchronizedBuffers["A"], False)

        except:
            print("Error reinitializing plot")
            pass

    def logbook(self):
        logbook('Python Real-Time BSA', 'BSA Data',
                str(self.numpoints) + ' points', self.plot.plotItem)
        self.statusBar().showMessage('Sent to LCLS Physics Logbook!', 10000)

    def MCCLog(self):
        MCCLog('/tmp/RTBSA.png', '/tmp/RTBSA.ps', self.plot.plotItem)

    def clearCallbacks(self, device):
        try:
            self.pvObjects[device].clear_callbacks()
            self.pvObjects[device].disconnect()
        except:
            self.statusBar().showMessage('Error clearing callbacks')

    def clearBuffers(self, device):
        self.rawBuffers[device] = []
        self.filteredBuffers[device] = []
        self.synchronizedBuffers[device] = []
    
    def stop(self):
        self.abort = True
        self.statusBar().showMessage('Stopped')
        self.ui.draw_button.setDisabled(False)
        QApplication.processEvents()

        self.clearCallbacks("A")
        self.clearCallbacks("B")

    def create_menu(self):

        load_file_action = self.create_action("&Save plot", shortcut="Ctrl+S",
                                              slot=self.save_plot,
                                              tip="Save the plot")

        quit_action = self.create_action("&Quit", slot=self.close,
                                         shortcut="Ctrl+Q",
                                         tip="Close the application")

        self.add_actions(self.file_menu, (load_file_action, None, quit_action))

        about_action = self.create_action("&About", shortcut='F1',
                                          slot=self.on_about, tip='About')

        self.add_actions(self.help_menu, (about_action,))

    @staticmethod
    def add_actions(target, actions):
        for action in actions:
            if action is None:
                target.addSeparator()
            else:
                target.addAction(action)

    def create_action(self, text, slot=None, shortcut=None, icon=None, tip=None,
                      checkable=False, signal="triggered()"):

        action = QAction(text, self)

        if icon is not None:
            action.setIcon(QIcon(":/%s.png" % icon))
        if shortcut is not None:
            action.setShortcut(shortcut)
        if tip is not None:
            action.setToolTip(tip)
            action.setStatusTip(tip)
        if slot is not None:
            self.connect(action, SIGNAL(signal), slot)
        if checkable:
            action.setCheckable(True)
        return action

    def save_plot(self):
        file_choices = "PNG (*.png)|*.png"
        # noinspection PyTypeChecker,PyCallByClass
        path = unicode(QFileDialog.getSaveFileName(self, 'Save file', '',
                                                   file_choices))
        if path:
            self.ui.widget.canvas.print_figure(path, dpi=100)
            self.statusBar().showMessage('Saved to %s' % path, 2000)

    def on_about(self):
        msg = ("Can you read this?  If so, congratulations. You are a magical, "
               + "marvelous troll.")
        # noinspection PyCallByClass
        QMessageBox.about(self, "About", msg.strip())


# TODO I bless the rains down in Africa!
def main():
    app = QApplication(sys.argv)
    window = RTBSA()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
