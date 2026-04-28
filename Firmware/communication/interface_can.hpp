#ifndef __INTERFACE_CAN_HPP
#define __INTERFACE_CAN_HPP

//#include <cmsis_os.h>
//#include "odrive_main.h"
//#include "can_helpers.hpp"
//#include <communication/can/can_simple.hpp>
//// Other protocol implementations here

typedef struct {
    uint32_t rx_input_cnt;
    uint32_t rx_process_cnt;
    uint32_t tx_low_reject_cnt;
    uint32_t tx_med_reject_cnt;
    uint32_t tx_high_reject_cnt;
    uint32_t tx_process_cnt;
    uint32_t can_error_cnt;
} CANStats_t;

extern CANStats_t can_stats_;


#endif
