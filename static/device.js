// Device + camera capability detection for smart capture guidance.
// Pure JS, no models, instant — decides whether to nudge the user toward the rear
// camera (best for palm) or toward face (on laptops / poor webcams). The palm
// modality is sharpest on a phone's rear camera; this surfaces that *before* capture.
(function () {
  "use strict";

  function isMobile() {
    const ua = navigator.userAgentData && navigator.userAgentData.mobile;
    if (typeof ua === "boolean") return ua;
    if (/Mobi|Android|iPhone|iPod|Windows Phone/i.test(navigator.userAgent)) return true;
    // iPadOS reports as "Macintosh" but is touch-first.
    return navigator.maxTouchPoints > 1 && /Macintosh/i.test(navigator.userAgent);
  }

  async function videoInputs() {
    try {
      const devs = await navigator.mediaDevices.enumerateDevices();
      return devs.filter((d) => d.kind === "videoinput");
    } catch (_) {
      return [];
    }
  }

  // Does a rear/"environment" camera likely exist? Phones expose 2+ video inputs;
  // labels (available once camera permission is granted) confirm a back camera.
  async function hasRearCamera() {
    const cams = await videoInputs();
    if (cams.some((c) => /back|rear|environment/i.test(c.label))) return true;
    return isMobile() && cams.length >= 2;
  }

  function trackInfo(stream) {
    const t = stream && stream.getVideoTracks && stream.getVideoTracks()[0];
    if (!t || !t.getSettings) return { facing: null, width: 0, height: 0 };
    const s = t.getSettings();
    return { facing: s.facingMode || null, width: s.width || 0, height: s.height || 0 };
  }

  // Guidance for using PALM given the device + the currently-open camera.
  //   action: "switch-rear" -> offer a one-tap switch to the back camera
  //           "use-face"    -> low-quality/laptop: face is the better bet here
  //           null          -> already optimal (phone on rear camera)
  async function palmAdvice(stream) {
    const { facing, width } = trackInfo(stream);
    const mobile = isMobile();
    const rear = await hasRearCamera();

    if (!mobile) {
      return { action: "use-face",
        text: "On a computer, face works best. For palm, a phone’s back camera is far sharper." };
    }
    if (rear && facing !== "environment") {
      return { action: "switch-rear",
        text: "Scanning a palm? Your back camera is sharper — tap to switch." };
    }
    if (facing === "environment" && width && width < 640) {
      return { action: "use-face",
        text: "This camera is low-resolution — face will be more reliable than palm." };
    }
    return null; // phone on a decent rear camera: optimal for palm
  }

  window.DeviceGuide = { isMobile, hasRearCamera, trackInfo, palmAdvice };
})();
