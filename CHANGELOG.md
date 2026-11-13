# Changelog

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
