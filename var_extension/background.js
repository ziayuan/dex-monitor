
// background.js
// 运行在后台，负责发起 HTTP 请求（绕过 Mixed Content 限制）

const LOCAL_SERVER = "http://127.0.0.1:8001/update";

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === "FORWARD_TO_LOCAL") {
        fetch(LOCAL_SERVER, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: message.data
        }).catch(err => {
            console.warn("Background forward failed:", err);
        });
    }
});
