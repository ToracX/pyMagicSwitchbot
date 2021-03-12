"""
Library to control MagicSwitchbot devices (chinese clone of SwitchBot)
"""
import time
import binascii
import logging
import random
from bluepy import btle
from Crypto.Cipher import AES

DEFAULT_RETRY_COUNT = 0
DEFAULT_RETRY_TIMEOUT = 0.2
NOTIFICATION_TIMEOUT = 5

logging.basicConfig(level=logging.INFO)
_LOGGER = logging.getLogger(__name__)


class MagicSwitchbotDelegate (btle.DefaultDelegate):
    """MagicSwitchbotDelegate
    
    Bluetooth notifications subscription to handle responses from the MagicSwitchbot device
    """

    def __init__(self, readHandle):
        """Class constructor
        
        This constructor must receive the handle of the characteristic we want to subscribe to.
        Parameters
        ----------
        readHandle : int
            Handle to the bluetooth characteristic we want to subscribe
        """
        btle.DefaultDelegate.__init__(self)
        self._readHandle = readHandle
        self._data = None
        self._received = False
        _LOGGER.info("Notification handler for MagicSwtichbot initialized")
      
    def resetData(self):
        """Resets the received data through the characteristic
        """
        self._data = None
        self._received = False
        _LOGGER.info("Resetting received data")
      
    def hasData(self) -> bool:
        """Check if data received
      
        This method checks if we received any data after latest reset
      
        Returns
        -------
            bool
                Returns True if there is data present
        """
        return self._received
    
    def getData(self) -> str:
        """Gets the received data
      
        This method retrieves the data sent by the device to the client
        in response to the latest command issued.
      
        Returns
        -------
            str
                Hexadecimal representation of the received data
        """
        return self._data.hex()
      
    def handleNotification(self, cHandle, data):
        """Notifications handler
      
        This method manages all notifications received from the MagicSwitchbot device
        We must filter those notifications that we expect in a normal protocol life cycle
      
        Parameters
        ----------
        cHandle : int
            Handle to the characteristic that sends the notification data 
          
        data : bytes
            Data that the characteristic sends
        """
        if (cHandle == self._readHandle):
            '''Filter our notified data'''
            _LOGGER.debug("Received data from device: %s", data)
            self._received = True
            self._data = data
        else:
            '''Discard all other notifications'''
            _LOGGER.debug("Received data from device at unexpected handle %d: %s", cHandle, data)
            btle.DefaultDelegate.handleNotification(self, cHandle, data)


class MagicSwitchbotDevice:
    """Base Representation of a MagicSwitchbot Device."""

    """Constants definition for BLE communication"""    
    UUID_SERVICE = "0000fee7-0000-1000-8000-00805f9b34fb"
    UUID_USERWRITE_CHAR = "000036f5-0000-1000-8000-00805f9b34fb"
    UUID_USERREAD_CHAR = "000036f6-0000-1000-8000-00805f9b34fb"
    UUID_NOTIFY_SET = "00002902-0000-1000-8000-00805f9b34fb"
    
    """Symmetric encryption key used for AES"""
    CRYPT_KEY = [42, 97, 57, 92, 64, 85, 73, 81, 58, 90, 75, 98, 27, 109, 55, 53]
    
    """Protocol commands definition"""
    CMD_ENNOTIF = "0100"
    CMD_OTA = "0301"
    CMD_SWITCH = "0501"
    CMD_MODIFYPWD = "0504"
    CMD_TIMEDSWITCH = "0508"
    CMD_GETTOKEN = "0601"
    
    """Protocol parameters definition"""
    PAR_SWITCHON = "01"
    PAR_SWITCHOFF = "00"
    PAR_SWITCHTOGGLE = "02"
    PAR_OTA = "01"
    
    """Protocol response return code definition"""
    RC_SWITCH = "02"
    RC_MODIFYPWD = "05"
    RC_TIMEDSWITCH = "09"
    RC_TOKENOK = "02"
    RC_TOKENERR = "03"
    
    """Protocol response status definition"""
    STA_OK = "00"
    STA_ERR = "01"
    
    def __init__(self, mac, retry_count=DEFAULT_RETRY_COUNT, password="", interface=None) -> None:
        self._interface = interface
        self._mac = mac
        self._device = None
        self._service = None
        self._userReadChar = None
        self._userWriteChar = None
        self._cccdDescriptor = None
        self._retry_count = retry_count
        self._password = password
        self._token = None
        self._delegate = None

    def _connect(self) -> None:
        if self._device is not None:
            return
        try:
            _LOGGER.debug("Connecting to MagicSwitchbot at address %s...", self._mac)
            self._device = btle.Peripheral(self._mac,
                                                  btle.ADDR_TYPE_PUBLIC,
                                                  self._interface)
            _LOGGER.debug("Connected to MagicSwitchbot.\n")
            # time.sleep(0.5)
            
            '''Initialize service and characteristics handles to the device'''
            self._service = self._device.getServiceByUUID(self.UUID_SERVICE)
            self._userReadChar = self._service.getCharacteristics(self.UUID_USERREAD_CHAR)[0]
            self._cccdDescriptor = self._userReadChar.getDescriptors(forUUID=self.UUID_NOTIFY_SET)[0]
            self._userWriteChar = self._service.getCharacteristics(self.UUID_USERWRITE_CHAR)[0]

            '''Once we connected, let's enable the response notifications'''
            self._enableNotifications()
            
            self._connected = True
        except btle.BTLEDisconnectError:
            _LOGGER.error("Device disconnected during connection attempt. You can try to reconnect.")
            self._device = None
            raise
            
        except btle.BTLEException:
            _LOGGER.error("Failed to connect to MagicSwitchbot.", exc_info=True)
            self._device = None
            raise

    def _enableNotifications(self) -> bool:
        """Enable read notifications
        
        We establish how we receive the notifications from the device
        """
        readHandle = self._userReadChar.getHandle()

        notifOk = False

        _LOGGER.debug("Enabling notifications for userRead characteristic (%s). Handle: 0x%X", self.UUID_USERREAD_CHAR, readHandle)
        _LOGGER.debug("Client Characteristic Configuration Descriptor: %s. Handle: 0x%X", self.UUID_NOTIFY_SET, self._cccdDescriptor.handle)

        '''Subscribe to userRed characteristic notifications'''
        self._delegate = MagicSwitchbotDelegate(readHandle)
        self._device.withDelegate(self._delegate)

        '''Enable the notifications for the read characteristic'''
        try:
            res = self._cccdDescriptor.write(binascii.a2b_hex(self.CMD_ENNOTIF), withResponse=True)
            res = self._cccdDescriptor.read()
            _LOGGER.info("Notifications enabled. Res: %s\n", res)
            notifOk = True
        except btle.BTLEGattError as e:
            _LOGGER.error("Error enabling notifications: %s\n", str(e))

        return notifOk

    def _disconnect(self) -> None:
        if self._device is None:
            return
        _LOGGER.debug("Disconnecting from MagicSwitchBot")
        try:
            self._device.disconnect()
            _LOGGER.info("Client disconnected\n\n\n")
            self._token = None
        except btle.BTLEException:
            _LOGGER.warning("Error disconnecting from MagicSwitchbot.", exc_info=True)
        finally:
            self._device = None
            
    def _encrypt(self, data) -> str:
        """Encrypts data using AES128 ECB
        Parameters
        ----------
            data : str
                Hexadecimal representation of the data to encrypt

        Returns
        -------
            str
                Hexadecimal representation of encrypted data
        """
        _LOGGER.debug("Unencrypted command: %s", data)
        cipher = AES.new(bytes(bytearray(self.CRYPT_KEY)), AES.MODE_ECB)
        encrypted = cipher.encrypt(bytes.fromhex(data)).hex()
        _LOGGER.debug("Encrypted command: %s", encrypted)
        return encrypted
      
    def _decrypt(self, data) -> str:
        """Decrypts data using AES128 ECB
        Parameters
        ----------
            data : str
                Hexadecimal representation of the data to decrypt

        Returns
        -------
            str
                Hexadecimal representation of decrypted data
        """
        
        '''We need a byte string as the key to decrypt or encrypt'''
        decipher = AES.new(bytes(bytearray(self.CRYPT_KEY)), AES.MODE_ECB)
        return decipher.decrypt(bytes.fromhex(data)).hex()

    def _prepareCommand(self, command, parameter):
        """Prepare the command to send to the device
        
        Prepares an encrypted string based on a command and a parameter to send to the MagicSwitchBot device
        
        Parameters
        ----------
            command : str
                Hexadecimal representation of the command to send (usually 2 hex bytes, len 4)
            parameter: str
                Hexadecimal representation of the parameter(s) to send (variable length)

        Returns
        -------
            str
                Hexadecimal representation of the 16 encrypted bytes to send to the device
        """
        
        '''Hex form of the parameter length:'''
        parmLen = "{:02X}".format(int(len(parameter) / 2))
        
        if self._token is None:
            tok = ""
        else:
            tok = self._token

        '''
        We calculate how long must be the random tail of the command.
        Each hex byte has a length of 2 characters, so the complete payload has 32 chars. The length byte also counts
        '''
        rndLen = 32 - len(command) - len(parameter) - len(tok) - 2
        rndTail = ''.join([str(y) for _ in range(rndLen) for y in random.choice('0123456789abcdef')])
        
        fullCommand = command + parmLen + parameter + tok + rndTail  

        return self._encrypt(fullCommand)

    def _writeData(self, data) -> bool:
        """Write data to the device
        
        This method writes data to the device via BLE, using the "userWrite" characteristic
        
        Parameters
        ----------
            data: str
                Hexadecimal string with 16 encryopted bytes of data to write

        Returns
        -------
            bool
                Returns True if the data is sent succesfully
        """
      
        self._delegate.resetData()
        _LOGGER.debug("Sending data, %s", data)
        write_result = self._userWriteChar.write(binascii.a2b_hex(data), True)
        
        if not write_result:
            _LOGGER.error("Sent command but didn't get a response from MagicSwitchbot confirming command was sent. "
                          "Please check the device.\n")
        else:
            _LOGGER.info("Successfully sent command to MagicSwitchbot (MAC: %s): %s\n", self._mac, write_result)
            
        return write_result

    def _sendCommand(self, command, parameter, retry, last=True) -> bool:
        
        '''First of all we check if there is no token retrieve'''
        if command != self.CMD_GETTOKEN and self._token is None:
            '''If the command is NOT GETTOKEN, we'll issue a GETOTKEN command before sending the actual command'''
            go = self._sendCommand(self.CMD_GETTOKEN, self._password, retry, False)
        else:
            go = True
        
        if go:
            send_success = False
            resp_success = False
            encrypted_command = self._prepareCommand(command, parameter)
            
            _LOGGER.debug("Sending command to Magicswitchbot %s", encrypted_command)
            try:
                if self._device is None:
                    self._connect()
                  
                send_success = self._writeData(encrypted_command)
                if send_success:
                    ''' Wait for a response'''
                    
                    _LOGGER.info("Waiting for notifications from the device...")
                    
                    while not self._delegate.hasData():
                        if self._device.waitForNotifications(1.0):
                            continue
                        _LOGGER.warn("Waiting...")
                    _LOGGER.info("Finished waiting for data")
                    
                    encrypted_response = self._delegate.getData()
                    _LOGGER.info("Data received: %s", encrypted_response)
                    
                    plain_response = self._decrypt(encrypted_response)
                    _LOGGER.info("Unencrypted result: %s", plain_response)
                    resp_success = self._processResponse(plain_response)
            except btle.BTLEException as e:
                _LOGGER.warning("MagicSwitchbot communication error: %s", str(e))
            finally:
                if last:
                    '''If it's the last command, we disconnect'''
                    self._disconnect()
                
            if resp_success:
                return True
            if retry < 1:
                _LOGGER.error("MagicSwitchbot communication failed. We won't try again.", exc_info=True)
                return False
            else:
                _LOGGER.warning("Cannot send command to MagicSwitchbot. Retrying (remaining: %d)...\n\n", retry)
    
            time.sleep(DEFAULT_RETRY_TIMEOUT)
            return self._sendCommand(command, parameter, retry - 1)
        else:
            return False
      
    def _processResponse(self, response) -> bool:
        """Process the response from the device
      
        This method processes the response that we receive from the device after
        executing a command
      
        Parameters
        ----------
            response : str
                Hexadecimal representation of the 16 byte response
                
        Return
        ------
            bool
                Returns True if the command result is succesfull
        """
        success = False
        command = response[0:2]
        ret_code = response[2:4]
        param_length = int(response[4:6])
        param = response[6:(6 + 2 * param_length)]
      
        _LOGGER.debug("Command: %s, Return Code: %s, Length: %d, Param: %s", command, ret_code, param_length, param)
      
        if command == self.CMD_GETTOKEN[0:2]:
            if ret_code == self.RC_TOKENOK:
                token = param[0:8]
                chip_type = param[8:10]
                ver_major = int(param[10:12])
                ver_minor = int(param[12:14])
                dev_type = param[14:16]
                en_pwd = param[16:18]
                self._token = token 
                _LOGGER.info("The current connection token is %s", token)
                _LOGGER.info("Chip type: %s, Firmware version: %d.%d, Device type: %s, Password enabled: %s", chip_type, ver_major, ver_minor, dev_type, en_pwd)
                success = True
            else:
                _LOGGER.error("Error retrieving token")
        elif command == self.CMD_SWITCH[0:2]:
            if ret_code == self.RC_SWITCH and param == self.STA_OK:
                _LOGGER.info("Switch state changed successfully")
                success = True
            else:
                _LOGGER.error("Error changing switch state")
                
        return success 


class MagicSwitchbot(MagicSwitchbotDevice):
    """Representation of a MagicSwitchbot."""
    
    def turn_on(self) -> bool:
        """Turn device on."""
        return self._sendCommand(self.CMD_SWITCH, self.PAR_SWITCHON, self._retry_count)

    def turn_off(self) -> bool:
        """Turn device off."""
        return self._sendCommand(self.CMD_SWITCH, self.PAR_SWITCHOFF, self._retry_count)
      
    def toggle(self) -> bool:
        """Toggle device state."""
        return self._sendCommand(self.CMD_SWITCH, self.PAR_SWITCHTOGGLE, self._retry_count)
