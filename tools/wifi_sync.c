/* wifi-sync UDID on|off|status
 * Toggles "Sync with this iPhone over Wi-Fi" (lockdown domain com.apple.mobile.wireless_lockdown).
 * Must be run while the phone is connected over USB and already paired. */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <libimobiledevice/libimobiledevice.h>
#include <libimobiledevice/lockdown.h>
#include <plist/plist.h>

#define DOMAIN "com.apple.mobile.wireless_lockdown"
#define KEY "EnableWifiConnections"

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s UDID on|off|status\n", argv[0]);
        return 2;
    }
    idevice_t dev = NULL;
    if (idevice_new_with_options(&dev, argv[1], IDEVICE_LOOKUP_USBMUX) != IDEVICE_E_SUCCESS) {
        fprintf(stderr, "ERROR: device not found over USB\n");
        return 1;
    }
    lockdownd_client_t c = NULL;
    lockdownd_error_t le = lockdownd_client_new_with_handshake(dev, &c, "wifi-sync");
    if (le != LOCKDOWN_E_SUCCESS) {
        fprintf(stderr, "ERROR: lockdown handshake failed (%d). Is the phone unlocked and paired?\n", le);
        idevice_free(dev);
        return 1;
    }
    int rc = 0;
    if (strcmp(argv[2], "status") != 0) {
        int want = strcmp(argv[2], "on") == 0;
        plist_t v = plist_new_bool(want);
        le = lockdownd_set_value(c, DOMAIN, KEY, v);
        /* v is owned by the request now; freeing it here caused a double free. */
        if (le != LOCKDOWN_E_SUCCESS) {
            fprintf(stderr, "ERROR: set_value failed (%d)\n", le);
            rc = 1;
        }
    }
    plist_t cur = NULL;
    if (lockdownd_get_value(c, DOMAIN, KEY, &cur) == LOCKDOWN_E_SUCCESS && cur) {
        uint8_t b = 0;
        if (plist_get_node_type(cur) == PLIST_BOOLEAN) plist_get_bool_val(cur, &b);
        printf("%s=%s\n", KEY, b ? "true" : "false");
        plist_free(cur);
    } else {
        printf("%s=unset\n", KEY);
    }
    lockdownd_client_free(c);
    idevice_free(dev);
    return rc;
}
