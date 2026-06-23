import { VideoRTC } from "@/lib/vendor/video-rtc.js";

/**
 * Registers the <dss-mse> custom element — go2rtc's VideoRTC forced to
 * MSE-only, muted + autoplay (so Chrome's autoplay policy lets it start with no
 * user gesture). Idempotent; safe to call from module scope.
 */
let registered = false;
export function registerDssMse() {
  if (registered || customElements.get("dss-mse")) {
    registered = true;
    return;
  }
  customElements.define(
    "dss-mse",
    class extends VideoRTC {
      oninit() {
        super.oninit();
        if (this.video) {
          this.video.controls = false;
          this.video.muted = true;
          this.video.playsInline = true;
          this.video.autoplay = true;
        }
      }
    },
  );
  registered = true;
}
