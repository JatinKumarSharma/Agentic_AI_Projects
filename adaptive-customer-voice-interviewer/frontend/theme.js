(function () {
    const STORAGE_KEY = "cvi-theme";

    const root = document.documentElement;
    const button = document.getElementById("themeToggle");
    const label = document.getElementById("themeToggleLabel");
    const icon = document.getElementById("themeToggleIcon");
    const themeColor = document.querySelector('meta[name="theme-color"]');
    const systemLight = window.matchMedia
        ? window.matchMedia("(prefers-color-scheme: light)")
        : null;

    function readSavedTheme() {
        try {
            return localStorage.getItem(STORAGE_KEY);
        } catch (error) {
            return null;
        }
    }

    function saveTheme(theme) {
        try {
            localStorage.setItem(STORAGE_KEY, theme);
        } catch (error) {
            // Theme still works for this page when storage is unavailable.
        }
    }

    function applyTheme(theme, persist) {
        const isLight = theme === "light";

        root.dataset.theme = isLight ? "light" : "dark";

        if (button) {
            button.setAttribute(
                "aria-label",
                isLight ? "Switch to dark theme" : "Switch to light theme"
            );
            button.setAttribute("aria-pressed", String(isLight));
            button.title = isLight
                ? "Switch to night theme"
                : "Switch to day theme";
        }

        if (label) {
            label.textContent = isLight ? "Light" : "Dark";
        }

        if (icon) {
            icon.textContent = isLight ? "☀" : "☾";
        }

        if (themeColor) {
            themeColor.setAttribute(
                "content",
                isLight ? "#f4f7f2" : "#090d12"
            );
        }

        if (persist) {
            saveTheme(root.dataset.theme);
        }
    }

    const savedTheme = readSavedTheme();
    const initialTheme = (
        savedTheme === "light" || savedTheme === "dark"
    )
        ? savedTheme
        : (systemLight && systemLight.matches ? "light" : "dark");

    applyTheme(initialTheme, false);

    if (button) {
        button.addEventListener("click", () => {
            const nextTheme = root.dataset.theme === "light"
                ? "dark"
                : "light";

            applyTheme(nextTheme, true);
        });
    }

    if (systemLight && !savedTheme) {
        const handleSystemChange = (event) => {
            applyTheme(event.matches ? "light" : "dark", false);
        };

        if (systemLight.addEventListener) {
            systemLight.addEventListener("change", handleSystemChange);
        } else if (systemLight.addListener) {
            systemLight.addListener(handleSystemChange);
        }
    }
})();
