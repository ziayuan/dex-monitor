// ==UserScript==
// @name         Variational Quotes Interceptor
// @namespace    http://tampermonkey.net/
// @version      1.0
// @description  Intercepts Variational indicative prices and streams them to localhost
// @author       You
// @match        https://omni.variational.io/*
// @grant        none
// ==/UserScript==

(function () {
    'use strict';

    console.log("[VAR-Interceptor] Initializing...");

    let ws = null;
    let wsConnected = false;
    let reconnectTimeout = null;

    // Connect to the Python local websocket server
    function connectWS() {
        if (wsConnected) return;

        // We will host our receiver on port 8001
        ws = new WebSocket('ws://127.0.0.1:8001');

        ws.onopen = () => {
            console.log("[VAR-Interceptor] Connected to Local Python Server.");
            wsConnected = true;
        };

        ws.onclose = () => {
            if (wsConnected) {
                console.log("[VAR-Interceptor] Disconnected from Local Server. Reconnecting in 2s...");
            }
            wsConnected = false;
            clearTimeout(reconnectTimeout);
            reconnectTimeout = setTimeout(connectWS, 2000);
        };

        ws.onerror = (err) => {
            // console.error("[VAR-Interceptor] WS Error:", err);
            ws.close();
        };
    }

    connectWS();

    // Override the native fetch API to intercept JSON responses
    const originalFetch = window.fetch;
    window.fetch = async function (...args) {
        // Await the actual fetch response
        const response = await originalFetch.apply(this, args);

        try {
            // Clone the response so the original webpage can still read it
            const clone = response.clone();
            const url = args[0];

            // Check if this fetch was for the indicative quotes
            if (typeof url === 'string' && url.includes('/api/quotes/indicative')) {
                clone.json().then(data => {
                    // Extract exact symbol and price details
                    // The payload might look like: {"bid":"64000.5", "ask":"64001.2", "instrument":{"underlying":"BTC"}}

                    if (data && data.instrument && data.instrument.underlying) {
                        const underlying = data.instrument.underlying;
                        const symbol = underlying + "-USD"; // e.g., "BTC-USD"

                        const payload = {
                            symbol: symbol,
                            bid: parseFloat(data.bid || 0),
                            ask: parseFloat(data.ask || 0),
                            mark_price: parseFloat(data.mark_price || 0)
                        };

                        // Push it down to Python!
                        if (wsConnected && ws.readyState === WebSocket.OPEN) {
                            ws.send(JSON.stringify(payload));
                            // console.log(`[VAR-Interceptor] Sent ${symbol} -> Bid: ${payload.bid}, Ask: ${payload.ask}`);
                        }
                    }
                }).catch(err => {
                    console.error("[VAR-Interceptor] Error parsing JSON clone:", err);
                });
            }
        } catch (e) {
            console.error("[VAR-Interceptor] Interception error:", e);
        }

        // Return the original pristine response back to the Variational site
        return response;
    };

    console.log("[VAR-Interceptor] Native Fetch Overridden. Ready to rock! 🎸");

})();
