import realtimeWorker from "./worker_realtime.js";
import { runScheduledOiSurgeWatch } from "./oi_surge_alert.js";

export default {
  async fetch(request, env, ctx) {
    return realtimeWorker.fetch(request, env, ctx);
  },
  async scheduled(controller, env, ctx) {
    // Preserve all existing POI / ENTRY scheduled behavior.
    realtimeWorker.scheduled(controller, env, ctx);
    // Scan one-fifth of the latest scan universe each minute, so the full
    // universe is checked for OI surge/acceleration every five minutes.
    ctx.waitUntil(
      runScheduledOiSurgeWatch(env, controller?.scheduledTime)
        .catch(e => console.error("OI surge watch failed", e))
    );
  }
};
