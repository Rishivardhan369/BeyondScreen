document.addEventListener("DOMContentLoaded", () => {
    const form = document.querySelector("[data-loading-form]");
    if (form) {
        form.addEventListener("submit", () => {
            if (!form.checkValidity()) return;
            const button = form.querySelector("[data-loading-button]");
            button.disabled = true;
            button.classList.add("is-loading");
            button.querySelector("span").textContent = "Creating your postcard";
        });
    }

    const status = document.querySelector(".copy-status");
    document.querySelectorAll("[data-copy-target]").forEach((button) => {
        button.addEventListener("click", async () => {
            const target = document.getElementById(button.dataset.copyTarget);
            const text = target.innerText.trim();
            try {
                await navigator.clipboard.writeText(text);
                status.textContent = button.dataset.copyTarget === "reflection-content" ? "Reflection copied." : "Postcard copied.";
            } catch {
                const area = document.createElement("textarea");
                area.value = text;
                document.body.appendChild(area);
                area.select();
                document.execCommand("copy");
                area.remove();
                status.textContent = "Copied to clipboard.";
            }
        });
    });

    // Message handling
    const messages = document.querySelectorAll('.message');
    messages.forEach((message) => {
        // Auto-dismiss after 5 seconds
        setTimeout(() => {
            if (!message.classList.contains('hiding')) {
                message.classList.add('hiding');
                message.addEventListener('animationend', () => {
                    message.remove();
                });
            }
        }, 5000);

        // Manual dismiss
        const closeBtn = message.querySelector('.btn-close');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => {
                message.classList.add('hiding');
                message.addEventListener('animationend', () => {
                    message.remove();
                });
            });
        }
    });
});
