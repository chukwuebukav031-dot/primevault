self.addEventListener("push", function(event) {
    if (!event.data) return;

    let data = {};

    try {
        data = event.data.json();
    } catch (error) {
        data = {
            title: "New message",
            body: event.data.text()
        };
    }

    const title = data.title || "New message";
    const options = {
        body: data.body || "You have a new message.",
        data: {
            url: data.url || "/"
        }
    };

    event.waitUntil(
        self.registration.showNotification(title, options)
    );
});

self.addEventListener("notificationclick", function(event) {
    event.notification.close();

    const url = event.notification.data &&
                event.notification.data.url
                ? event.notification.data.url
                : "/";

    event.waitUntil(
        clients.matchAll({
            type: "window",
            includeUncontrolled: true
        }).then(function(clientList) {
            for (const client of clientList) {
                if ("focus" in client) {
                    client.navigate(url);
                    return client.focus();
                }
            }

            if (clients.openWindow) {
                return clients.openWindow(url);
            }
        })
    );
});
