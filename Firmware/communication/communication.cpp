
/* Includes ------------------------------------------------------------------*/

#include "communication.h"

#include "interface_usb.h"
#include "interface_uart.h"
#include "interface_can.hpp"
#include "interface_i2c.h"

#include "odrive_main.h"
#include "freertos_vars.h"
#include "utils.hpp"

#include <cmsis_os.h>
#include <memory>
//#include <usbd_cdc_if.h>
//#include <usb_device.h>
//#include <usart.h>
#include <gpio.h>

#include <type_traits>

/* Private defines -----------------------------------------------------------*/
/* Private macros ------------------------------------------------------------*/
/* Private typedef -----------------------------------------------------------*/
/* Global constant data ------------------------------------------------------*/
/* Global variables ----------------------------------------------------------*/

uint64_t serial_number;
char serial_number_str[13]; // 12 digits + null termination

/* Private constant data -----------------------------------------------------*/
/* Private variables ---------------------------------------------------------*/
/* Private function prototypes -----------------------------------------------*/
/* Function implementations --------------------------------------------------*/

void init_communication(void) {
    //printf("hi!\r\n");

    // Dual UART operation not supported yet
    if (odrv.config_.enable_uart_a && odrv.config_.enable_uart_b) {
        odrv.misconfigured_ = true;
    }

    if (odrv.config_.enable_uart_a && uart_a) {
        start_uart_server(uart_a);
    } else if (odrv.config_.enable_uart_b && uart_b) {
        start_uart_server(uart_b);
    }

    start_usb_server();

    if (odrv.config_.enable_i2c_a) {
        start_i2c_server();
    }

    if (odrv.config_.enable_can_a) {
        odrv.can_.start_server(&hcan1);
    }
}

#include <fibre/async_stream.hpp>


extern "C" {
int _write(int file, const char* data, int len) __attribute__((used));
}

// @brief This is what printf calls internally
int _write(int file, const char* data, int len) {
    fibre::cbufptr_t buf{(const uint8_t*)data, (const uint8_t*)data + len};

    if (odrv.config_.uart0_protocol == ODrive::STREAM_PROTOCOL_TYPE_STDOUT ||
        odrv.config_.uart0_protocol == ODrive::STREAM_PROTOCOL_TYPE_ASCII_AND_STDOUT) {
        uart0_stdout_sink.write(buf);
        if (!uart0_stdout_pending) {
            uart0_stdout_pending = true;
            osMessagePut(uart_event_queue, 3, 0);
        }
    }

    if (odrv.config_.usb_cdc_protocol == ODrive::STREAM_PROTOCOL_TYPE_STDOUT ||
        odrv.config_.usb_cdc_protocol == ODrive::STREAM_PROTOCOL_TYPE_ASCII_AND_STDOUT) {
        usb_cdc_stdout_sink.write(buf);
        if (!usb_cdc_stdout_pending) {
            usb_cdc_stdout_pending = true;
            osMessagePut(usb_event_queue, 7, 0);
        }
    }

    return len; // Always pretend that we processed everything
}


#include "../autogen/function_stubs.hpp"

ODrive& ep_root = odrv;
#include "../autogen/endpoints.hpp"

#include "../communication/can/can_simple.hpp"

// SDO endpoint access implementations - delegate to Fibre introspection system
bool CANSimple::sdo_get_property(uint16_t endpoint_id, char* out_buf, size_t out_size) {
    Introspectable property{};
    fibre::get_property(property, endpoint_id);
    if (!property.is_valid()) return false;
    const StringConvertibleTypeInfo* type_info =
        dynamic_cast<const StringConvertibleTypeInfo*>(property.get_type_info());
    if (!type_info) return false;
    return type_info->get_string(property, out_buf, out_size);
}

bool CANSimple::sdo_set_property(uint16_t endpoint_id, const char* value, size_t len) {
    Introspectable property{};
    fibre::get_property(property, endpoint_id);
    if (!property.is_valid()) return false;
    const StringConvertibleTypeInfo* type_info =
        dynamic_cast<const StringConvertibleTypeInfo*>(property.get_type_info());
    if (!type_info) return false;
    return type_info->set_string(property, const_cast<char*>(value), len);
}
