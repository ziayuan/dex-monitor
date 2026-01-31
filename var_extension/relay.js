
// relay.js
// 运行在 ISOLATED world，负责将 MAIN world 的消息转发给 Background
// 需要在 manifest 中注册

window.addEventListener("message", function (event) {
    // 只接受来自本页面的消息
    if (event.source !== window) return;

    if (event.data.type && (event.data.type === "VAR_WS_DATA" || event.data.type === "PARA_WS_DATA")) {
        // 转发给 background.js
        try {
            chrome.runtime.sendMessage({
                type: "FORWARD_TO_LOCAL",
                data: event.data.payload,
                source: event.data.type // e.g. "VAR_WS_DATA"
            });
        } catch (e) {
            // 插件可能被重载后失效，忽略
        }
    }
});
