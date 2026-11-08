import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import esp32_ble
from esphome.const import CONF_ID
DEPENDENCIES = ["esp32_ble"]
ns = cg.esphome_ns.namespace("airvalent_pair_observer")
Observer = ns.class_("Observer", cg.Component)
CONFIG_SCHEMA = cv.Schema({
    cv.GenerateID(): cv.declare_id(Observer),
    cv.GenerateID("ble_id"): cv.use_id(esp32_ble.ESP32BLE),
    cv.Required("address"): cv.mac_address,
}).extend(cv.COMPONENT_SCHEMA)
async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    cg.add(var.set_address(config["address"].as_hex))
    parent = await cg.get_variable(config["ble_id"])
    esp32_ble.register_gap_event_handler(parent, var)
