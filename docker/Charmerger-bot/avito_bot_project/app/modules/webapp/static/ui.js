// static/ui.js
const tg = window.Telegram.WebApp; // tg нужен для стилей и showAlert

export function applyThemeStyles() {
    if (!tg.themeParams) return;
    document.body.style.backgroundColor = tg.themeParams.bg_color || '#ffffff';
    document.body.style.color = tg.themeParams.text_color || '#000000';
    document.querySelectorAll('button:not(.secondary):not(.danger)').forEach(btn => {
        btn.style.backgroundColor = tg.themeParams.button_color || '#2481cc';
        btn.style.color = tg.themeParams.button_text_color || '#ffffff';
    });
    document.querySelectorAll('input, textarea, select').forEach(el => {
        el.style.borderColor = tg.themeParams.hint_color || '#999999';
    });
}

export function openTab(event, tabName, isDefault = false) {
    // ... (код функции openTab как был)
    let i, tabcontent, tablinks;
    tabcontent = document.getElementsByClassName("tab-content");
    for (i = 0; i < tabcontent.length; i++) {
        tabcontent[i].style.display = "none";
        tabcontent[i].classList.remove("active");
    }
    tablinks = document.getElementsByClassName("tab-button");
    for (i = 0; i < tablinks.length; i++) {
        tablinks[i].classList.remove("active");
    }
    const activeTabContent = document.getElementById(tabName);
    if (activeTabContent) {
        activeTabContent.style.display = "block";
        activeTabContent.classList.add("active");
    }
    if (event && event.currentTarget) {
      event.currentTarget.classList.add("active");
    } else if (isDefault) { 
        const defaultTabButton = Array.from(tablinks).find(
            btn => btn.getAttribute('onclick') && btn.getAttribute('onclick').includes(`'${tabName}'`)
        );
        if (defaultTabButton) defaultTabButton.classList.add("active");
    }
}

window.openTab = openTab;

export function showLoading(show) {
    const indicator = document.getElementById('loadingIndicator');
    if (indicator) indicator.style.display = show ? 'flex' : 'none';
}

export function escapeHtml(unsafe) {
    // ... (исправленный код функции escapeHtml)
    if (typeof unsafe !== 'string') {
        if (unsafe === null || typeof unsafe === 'undefined') return '';
        return String(unsafe);
    }
    return unsafe
        .replace(/&/g, "&amp;")  // Важно делать это первой заменой!
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

export function escapeJs(unsafe) { 
    // ... (код функции escapeJs)
    if (typeof unsafe !== 'string') {
        if (unsafe === null || typeof unsafe === 'undefined') return '';
        return String(unsafe);
    }
      return unsafe
    .replace(/\\/g, '\\\\')  // сначала экранируем обратные слеши
    .replace(/'/g, "\\'")    // затем одинарные кавычки
    .replace(/"/g, '\\"')     // двойные кавычки
    .replace(/\n/g, '\\n')    // переносы строки
    .replace(/\r/g, '\\r')    // возврат каретки
    .replace(/\t/g, '\\t');   // табуляция
}
