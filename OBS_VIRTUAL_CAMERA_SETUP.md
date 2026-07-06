# Setting Up the OBS Virtual Camera (for Video Feed mode)

Video Feed mode draws OBS's Virtual Camera behind the captions, so the
output window shows exactly what's live on your stream with translated
text overlaid on top. This requires OBS Studio to be running and its
Virtual Camera started before you enable Video Feed in this app.

## 1. Install OBS Studio

Download and install OBS Studio (free) from https://obsproject.com if you
don't already have it.

## 2. Set up your OBS scene

Build your normal stream scene in OBS (camera, slides, whatever you send
to YouTube/Facebook Live). Video Feed mode mirrors whatever OBS is
compositing as its "Program" output, so make sure the scene looks the way
you want it to appear behind the captions.

## 3. Start the Virtual Camera

In OBS, find the **Start Virtual Camera** button (bottom-right of the main
window, in the Controls dock). Click it. OBS now exposes itself as a
camera device (e.g. "OBS Virtual Camera") that other apps, including this
one, can open like any webcam.

Leave OBS running with the Virtual Camera started for the whole time you
want Video Feed mode active.

## 4. Confirm the Output Type is "Program"

Click the small cogwheel icon to the right of the **Start Virtual Camera**
button and check that **Output Type** is set to **Program** (this is the
default). This makes the Virtual Camera a pixel-accurate mirror of
whatever OBS is actually streaming, not a Preview or a single fixed
Scene/Source. If it's set to anything else, the video feed shown behind
captions won't match what's live.

## 5. Match OBS's output resolution to the output window

In OBS, go to **Settings > Video** and set **Output (Scaled) Resolution**
to exactly the resolution of the monitor the output window will be shown
on (e.g. 1920x1080). This app draws the Virtual Camera frame at native
size with no rescaling whenever it already matches the output window's
size, so matching it here gives you an exact, unscaled passthrough of
OBS's stream. If they don't match, the app still displays the feed
correctly, just resized to fit the window.

## 6. Enable Video Feed mode in this app

1. Open the app's Settings window.
2. In the Display section, check **Show video feed behind captions**.
3. Under **Camera Device**, click **Refresh** to (re)scan for camera
   devices — do this after starting the OBS Virtual Camera, not before,
   or it won't be listed yet.
4. Select the entry corresponding to the OBS Virtual Camera.
5. Click **Apply**.

## Troubleshooting

- **Status shows "Camera N not found - start OBS Virtual Camera first"**:
  OBS's Virtual Camera isn't running. Go back to OBS and click **Start
  Virtual Camera**, then click **Refresh** in this app's settings.
- **Status shows "Camera feed lost"**: OBS was closed, the Virtual Camera
  was stopped, or the device was disconnected while the app was reading
  from it. Restart the Virtual Camera in OBS and re-select the device.
- **Video behind captions doesn't match the live stream**: check the
  Output Type via the cogwheel next to **Start Virtual Camera** and make
  sure it's set to **Program** (see step 4).
- **Video looks soft/rescaled**: the status line shows the resolution
  actually being served by OBS — if it doesn't match the output window's
  resolution, set OBS's Output (Scaled) Resolution to match (see step 5)
  for an exact passthrough instead of a resized copy.
- **Multiple cameras with similar names**: if you have more than one
  camera-like device, try each entry in the Camera Device dropdown and
  check the status text for a resolution match to confirm you picked the
  Virtual Camera and not a physical webcam.
