""""
  Library for the Maxim MAX30100 pulse oximetry system on Raspberry Pi

  Based on original C library for Arduino by Connor Huffine/Kontakt
  https: // github.com / kontakt / MAX30100

  September 2017
"""

import smbus

INT_STATUS   = 0x00  # Which interrupts are tripped
INT_ENABLE   = 0x01  # Which interrupts are active
FIFO_WR_PTR  = 0x02  # Where data is being written
OVRFLOW_CTR  = 0x03  # Number of lost samples
FIFO_RD_PTR  = 0x04  # Where to read from
FIFO_DATA    = 0x05  # Ouput data buffer
MODE_CONFIG  = 0x06  # Control register
SPO2_CONFIG  = 0x07  # Oximetry settings
LED_CONFIG   = 0x09  # Pulse width and power of LEDs
TEMP_INTG    = 0x16  # Temperature value, whole number
TEMP_FRAC    = 0x17  # Temperature value, fraction
REV_ID       = 0xFE  # Part revision
PART_ID      = 0xFF  # Part ID, normally 0x11

I2C_ADDRESS  = 0x57  # I2C address of the MAX30100 device


PULSE_WIDTH = {
    200: 0,
    400: 1,
    800: 2,
   1600: 3,
}

SAMPLE_RATE = {
    50: 0,
   100: 1,
   167: 2,
   200: 3,
   400: 4,
   600: 5,
   800: 6,
  1000: 7,
}

LED_CURRENT = {
       0: 0,
     4.4: 1,
     7.6: 2,
    11.0: 3,
    14.2: 4,
    17.4: 5,
    20.8: 6,
    24.0: 7,
    27.1: 8,
    30.6: 9,
    33.8: 10,
    37.0: 11,
    40.2: 12,
    43.6: 13,
    46.8: 14,
    50.0: 15
}

def _get_valid(d, value):
    try:
        return d[value]
    except KeyError:
        raise KeyError("Value %s not valid, use one of: %s" % (value, ', '.join([str(s) for s in d.keys()])))

def _twos_complement(val, bits):
    """compute the 2's complement of int value val"""
    if (val & (1 << (bits - 1))) != 0: # if sign bit is set e.g., 8bit: 128-255
        val = val - (1 << bits)
    return val

INTERRUPT_SPO2 = 0
INTERRUPT_HR = 1
INTERRUPT_TEMP = 2
INTERRUPT_FIFO = 3

MODE_HR = 0x02
MODE_SPO2 = 0x03


class MAX30100(object):

    def __init__(self,
                 i2c=None,
                 mode=MODE_HR,
                 sample_rate=100,
                 led_current_red=11.0,
                 led_current_ir=11.0,
                 pulse_width=1600,
                 max_buffer_len=10000
                 ):

        # Default to the standard I2C bus on Pi.
        self.i2c = i2c if i2c else smbus.SMBus(1)

        self.set_mode(MODE_HR)  # Trigger an initial temperature read.
        self.set_led_current(led_current_red, led_current_ir)
        self.set_spo_config(sample_rate, pulse_width)

        # Reflectance data (latest update)
        self.buffer_red = []
        self.buffer_ir = []

        self.max_buffer_len = max_buffer_len
        self._interrupt = None

    @property
    def red(self):
        return self.buffer_red[-1] if self.buffer_red else None

    @property
    def ir(self):
        return self.buffer_ir[-1] if self.buffer_ir else None

    def set_led_current(self, led_current_red=11.0, led_current_ir=11.0):
        # Validate the settings, convert to bit values.
        led_current_red = _get_valid(LED_CURRENT, led_current_red)
        led_current_ir = _get_valid(LED_CURRENT, led_current_ir)
        self.i2c.write_byte_data(I2C_ADDRESS, LED_CONFIG, (led_current_red << 4) | led_current_ir)

    def set_mode(self, mode):
        reg = self.i2c.read_byte_data(I2C_ADDRESS, MODE_CONFIG)
        self.i2c.write_byte_data(I2C_ADDRESS, MODE_CONFIG, reg & 0x74) # mask the SHDN bit
        self.i2c.write_byte_data(I2C_ADDRESS, MODE_CONFIG, reg | mode)

    def set_spo_config(self, sample_rate=100, pulse_width=1600):
        reg = self.i2c.read_byte_data(I2C_ADDRESS, SPO2_CONFIG)
        reg = reg & 0xFC  # Set LED pulsewidth to 00
        self.i2c.write_byte_data(I2C_ADDRESS, SPO2_CONFIG, reg | pulse_width)

    def enable_spo2(self):
        self.set_mode(MODE_SPO2)

    def disable_spo2(self):
        self.set_mode(MODE_HR)

    def enable_interrupt(self, interrupt_type):
        self.i2c.write_byte_data(I2C_ADDRESS, INT_ENABLE, (interrupt_type + 1)<<4)
        self.i2c.read_byte_data(I2C_ADDRESS, INT_STATUS)

    def get_number_of_samples(self):
        write_ptr = self.i2c.read_byte_data(I2C_ADDRESS, FIFO_WR_PTR)
        read_ptr = self.i2c.read_byte_data(I2C_ADDRESS, FIFO_RD_PTR)
        return abs(16+write_ptr - read_ptr) % 16

    def read_sensor(self):
        bytes = self.i2c.read_i2c_block_data(I2C_ADDRESS, FIFO_DATA, 4)
        # Add latest values.
        self.buffer_ir.append(bytes[0]<<8 | bytes[1])
        self.buffer_red.append(bytes[2]<<8 | bytes[3])
        # Crop our local FIFO buffer to length.
        self.buffer_red = self.buffer_red[-self.max_buffer_len:]
        self.buffer_ir = self.buffer_ir[-self.max_buffer_len:]

    def shutdown(self):
        reg = self.i2c.read_byte_data(I2C_ADDRESS, MODE_CONFIG)
        self.i2c.write_byte_data(I2C_ADDRESS, MODE_CONFIG, reg | 0x80)

    def reset(self):
        reg = self.i2c.read_byte_data(I2C_ADDRESS, MODE_CONFIG)
        self.i2c.write_byte_data(I2C_ADDRESS, MODE_CONFIG, reg | 0x40)

    def refresh_temperature(self):
        reg = self.i2c.read_byte_data(I2C_ADDRESS, MODE_CONFIG)
        self.i2c.write_byte_data(I2C_ADDRESS, MODE_CONFIG, reg | (1 << 3))

    def get_temperature(self):
        intg = _twos_complement(self.i2c.read_byte_data(I2C_ADDRESS, TEMP_INTG))
        frac = self.i2c.read_byte_data(I2C_ADDRESS, TEMP_FRAC)
        return intg + (frac * 0.0625)

    def get_rev_id(self):
        return self.i2c.read_byte_data(I2C_ADDRESS, REV_ID)

    def get_part_id(self):
        return self.i2c.read_byte_data(I2C_ADDRESS, PART_ID)

    def get_registers(self):
        return {
            "INT_STATUS": self.i2c.read_byte_data(I2C_ADDRESS, INT_STATUS),
            "INT_ENABLE": self.i2c.read_byte_data(I2C_ADDRESS, INT_ENABLE),
            "FIFO_WR_PTR": self.i2c.read_byte_data(I2C_ADDRESS, FIFO_WR_PTR),
            "OVRFLOW_CTR": self.i2c.read_byte_data(I2C_ADDRESS, OVRFLOW_CTR),
            "FIFO_RD_PTR": self.i2c.read_byte_data(I2C_ADDRESS, FIFO_RD_PTR),
            "FIFO_DATA": self.i2c.read_byte_data(I2C_ADDRESS, FIFO_DATA),
            "MODE_CONFIG": self.i2c.read_byte_data(I2C_ADDRESS, MODE_CONFIG),
            "SPO2_CONFIG": self.i2c.read_byte_data(I2C_ADDRESS, SPO2_CONFIG),
            "LED_CONFIG": self.i2c.read_byte_data(I2C_ADDRESS, LED_CONFIG),
            "TEMP_INTG": self.i2c.read_byte_data(I2C_ADDRESS, TEMP_INTG),
            "TEMP_FRAC": self.i2c.read_byte_data(I2C_ADDRESS, TEMP_FRAC),
            "REV_ID": self.i2c.read_byte_data(I2C_ADDRESS, REV_ID),
            "PART_ID": self.i2c.read_byte_data(I2C_ADDRESS, PART_ID),
        }
