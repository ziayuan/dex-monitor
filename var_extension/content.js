
// content.js
// 运行在页面 MAIN world，负责 Hook WebSocket
// 支持 Variational 和 Paradex

(function () {
    console.log("%c Monitor Hook Loaded (Relay Mode) ", "background: #222; color: #bada55; font-size: 20px");

    const OriginalWebSocket = window.WebSocket;

    window.WebSocket = function (url, protocols) {
        const ws = new OriginalWebSocket(url, protocols);

        // 1. Variational Portfolio
        if (url.includes("variational.io") && url.includes("portfolio")) {
            console.log("Hooked Var WS:", url);
            ws.addEventListener("message", function (event) {
                if (event.data && typeof event.data === 'string') {
                    // 无条件转发 Var 数据
                    window.postMessage({ type: "VAR_WS_DATA", payload: event.data }, "*");
                }
            });
        }

        // 2. Paradex Hook
        if (url.includes("paradex.trade")) {
            console.log("Hooked Paradex WS:", url);
            ws.addEventListener("message", function (event) {
                if (event.data && typeof event.data === 'string') {
                    try {
                        const data = JSON.parse(event.data);
                        // 数据结构: { jsonrpc: "2.0", method: "subscription", params: { channel: "positions", data: {...} } }
                        if (data.method === "subscription" &&
                            data.params &&
                            data.params.channel === "positions") {

                            console.log("Forwarding Paradex Position:", data.params.data.market);
                            window.postMessage({
                                type: "PARA_WS_DATA",
                                payload: event.data
                            }, "*");
                        }
                    } catch (e) { }
                }
            });
        }

        return ws;
    };

    window.WebSocket.prototype = OriginalWebSocket.prototype;
    window.WebSocket.CONNECTING = OriginalWebSocket.CONNECTING;
    window.WebSocket.OPEN = OriginalWebSocket.OPEN;
    window.WebSocket.CLOSING = OriginalWebSocket.CLOSING;
    window.WebSocket.CLOSED = OriginalWebSocket.CLOSED;

})();
