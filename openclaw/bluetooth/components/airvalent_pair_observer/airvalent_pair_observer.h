#pragma once
#include "esphome/core/component.h"
#include "esphome/core/log.h"
#include "esp_gap_ble_api.h"
namespace esphome::airvalent_pair_observer {
class Observer : public Component {
 public:
  void set_address(uint64_t address) { address_ = address; }
  bool matches(const uint8_t *a) {
    uint64_t value = 0;
    for (int i = 0; i < 6; i++) value = (value << 8) | a[i];
    return value == address_;
  }
  void gap_event_handler(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param) {
    if (event == ESP_GAP_BLE_PASSKEY_REQ_EVT && matches(param->ble_security.ble_req.bd_addr)) {
      ESP_LOGI("airvalent_pair", "Enrollment code requested by target");
    }
    if (event == ESP_GAP_BLE_AUTH_CMPL_EVT && matches(param->ble_security.auth_cmpl.bd_addr)) {
      auto &auth = param->ble_security.auth_cmpl;
      ESP_LOGI("airvalent_pair", "Authentication complete success=%d mode=%u failure=%u stored_bonds=%d",
               auth.success, unsigned(auth.auth_mode), auth.success ? 0U : unsigned(auth.fail_reason), esp_ble_get_bond_device_num());
    }
  }
 protected:
  uint64_t address_{0};
};
}
