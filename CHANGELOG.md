# Changelog

## 2.1.2

- Lights no longer fail now and then with a "Timed out waiting for a reply"
  error. Twice a minute the controller ignores requests for a few seconds;
  Home Assistant now sends such a request again, so it goes through after a
  short delay.
- A poll that still fails after these retries is logged as a warning.

## 2.1.1

- Turning on a non-dimmable light no longer shows a "Failed to control"
  error. The light already switched on; the error came from a brightness
  command the controller does not accept for on/off lights.

## 2.1.0

- Setup no longer has the **Import lights** step for pasting a list of lights.
  After you enter the controller's host, add your lights one by one with
  **Add light**. Lights you already have are not affected.

## 2.0.0

- First release. The Domestia integration connects Home Assistant to the
  controller directly over your local network.
- Lights are added, changed and removed in the Home Assistant UI.
- Lights become unavailable after 3 failed polls, and a repair issue reports
  configured relays that the controller does not have.
