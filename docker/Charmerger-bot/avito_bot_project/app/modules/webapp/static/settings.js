// static/settings.js
import { apiCall } from './api.js';
import { escapeHtml } from './ui.js';

const tg = window.Telegram.WebApp;

export async function loadSettings() {
    const currentUserTimezoneEl = document.getElementById('currentUserTimezone');
    const timezoneSelectEl = document.getElementById('timezoneSelect');

    currentUserTimezoneEl.textContent = 'Загрузка...';
    timezoneSelectEl.innerHTML = '<option value="">Загрузка...</option>';

    const settings = await apiCall('/api/user/settings');

    if (settings && typeof settings === 'object') {
        currentUserTimezoneEl.textContent = settings.timezone || 'Не установлен';
        populateTimezoneSelect(settings.available_timezones, settings.timezone);
    } else {
        currentUserTimezoneEl.textContent = 'Ошибка загрузки';
        timezoneSelectEl.innerHTML = '<option value="">Ошибка</option>';
    }
}

function populateTimezoneSelect(availableTimezones, currentTimezone) {
    const select = document.getElementById('timezoneSelect');
    select.innerHTML = '<option value="">-- Выберите часовой пояс --</option>';

    if (availableTimezones && typeof availableTimezones === 'object') {
        const timezonesArray = Object.entries(availableTimezones).map(([value, text]) => ({ value, text }));
        timezonesArray.sort((a, b) => {
            const gmtA = parseInt(a.text.match(/GMT([+-]\d+)/)?.[1] || "99");
            const gmtB = parseInt(b.text.match(/GMT([+-]\d+)/)?.[1] || "99");
            if (gmtA !== gmtB) return gmtA - gmtB;
            return a.text.localeCompare(b.text);
        });
        timezonesArray.forEach(tz => {
            const option = document.createElement('option');
            option.value = tz.value;
            option.textContent = tz.text;
            if (tz.value === currentTimezone) {
                option.selected = true;
            }
            select.appendChild(option);
        });
    }
}

export async function saveTimezone() {
    const select = document.getElementById('timezoneSelect');
    const selectedTimezone = select.value;
    if (!selectedTimezone) {
        tg.showAlert('Пожалуйста, выберите часовой пояс.');
        return;
    }
    // --- ИСПРАВЛЕНИЕ ПУТИ ЗДЕСЬ ---
    const result = await apiCall('/api/user/settings/timezone', 'POST', { timezone: selectedTimezone });
    if (result && result.success) {
        tg.HapticFeedback.notificationOccurred('success');
        document.getElementById('currentUserTimezone').textContent = selectedTimezone;
        tg.showAlert(result.message || 'Часовой пояс сохранен!');
    }
}

export function fullReset() {
    tg.showConfirm('Вы уверены? Это действие необратимо и удалит ВСЕ ваши данные, включая аккаунты Avito, шаблоны и правила.', async (confirmed) => {
        if (confirmed) {
            // --- ИСПРАВЛЕНИЕ ПУТИ ЗДЕСЬ ---
            const result = await apiCall('/user/full-reset', 'POST'); // Убрал /avito-accounts/
            if (result && result.success) {
                tg.showAlert('Все данные были сброшены. Это окно будет закрыто.');
                // Закрываем WebApp после успешного сброса
                setTimeout(() => tg.close(), 2000);
            }
        }
    });
}