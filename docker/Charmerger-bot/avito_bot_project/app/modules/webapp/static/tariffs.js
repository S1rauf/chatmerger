// static/tariffs.js
import { apiCall } from './api.js';
import { escapeHtml } from './ui.js';

const tg = window.Telegram.WebApp;

export async function loadTariffs() {
    const listDiv = document.getElementById('tariffsList');
    listDiv.innerHTML = `<p class="status-loading">Загрузка тарифов...</p>`;
    const tariffsData = await apiCall('/api/tariffs'); // <-- Переименовал для ясности

    if (!tariffsData || !Array.isArray(tariffsData)) {
        listDiv.innerHTML = `<p class="status-error">Не удалось загрузить тарифы.</p>`;
        return;
    }

    listDiv.innerHTML = '';
    tariffsData.forEach(tariff => {
        const card = document.createElement('div');
        card.className = `tariff-card ${tariff.is_current ? 'current' : ''}`;
        
        // --- НОВАЯ ЛОГИКА РЕНДЕРИНГА ФИЧ ---
        const featuresHtml = tariff.features.map(f => 
            // Добавляем класс 'inactive' для неактивных фич, чтобы их можно было стилизовать в CSS
            `<li class="${f.is_active ? 'active' : 'inactive'}">
                ${escapeHtml(f.text)}
            </li>`
        ).join('');

        // --- НОВАЯ ЛОГИКА ДЛЯ КНОПОК ---
        let buttonHtml;
        if (tariff.is_current) {
            buttonHtml = `<button disabled>✅ Ваш текущий тариф</button>`;
        } else if (tariff.price_rub <= 0) {
            // Для бесплатного тарифа кнопка тоже неактивна
            buttonHtml = `<button disabled>Бесплатный</button>`;
        } else {
            // Кнопка покупки для всех остальных
            buttonHtml = `<button class="js-purchase-tariff" data-id="${tariff.id}">🚀 Перейти на этот тариф</button>`;
        }
        
        // --- НОВАЯ СТРУКТУРА HTML С АККОРДЕОНОМ ---
        card.innerHTML = `
            <h3>${escapeHtml(tariff.name)}</h3>
            <div class="tariff-price">${tariff.price_rub > 0 ? tariff.price_rub + ' ₽' : 'Бесплатно'}<span>/ ${tariff.duration_days ? tariff.duration_days + ' дн.' : 'навсегда'}</span></div>
            <p class="hint">${escapeHtml(tariff.description)}</p>
            
            <div class="features-accordion">
                <button class="features-header">
                    <span>Подробные возможности</span>
                    <span class="arrow">▼</span>
                </button>
                <div class="features-content">
                    <ul class="tariff-features">${featuresHtml}</ul>
                </div>
            </div>
            
            <div class="tariff-action">
                ${buttonHtml}
            </div>
        `;
        listDiv.appendChild(card);
    });

    // --- НОВАЯ ЛОГИКА: ДОБАВЛЯЕМ ОБРАБОТЧИКИ ДЛЯ АККОРДЕОНОВ ---
    document.querySelectorAll('.features-header').forEach(header => {
        header.addEventListener('click', () => {
            header.parentElement.classList.toggle('open');
        });
    });
}

// ИСПРАВЛЕНИЕ: Добавляем `export` перед функцией
export async function purchaseTariff(tariffId) {
    tg.showConfirm(`Вы уверены, что хотите приобрести/продлить этот тариф? Сначала будет попытка списания с внутреннего баланса.`, async (confirmed) => {
        if (confirmed) {
            const result = await apiCall('/api/tariffs/purchase', 'POST', { tariff_id: tariffId });
            if (result && result.success) {
                tg.showAlert(result.message);
                // Можно добавить небольшую задержку и перезагрузку данных
                setTimeout(() => {
                    // Перезагружаем главную и вкладку тарифов
                    document.querySelector('.tab-button[data-tab="mainTab"]')?.click();
                    document.querySelector('.tab-button[data-tab="tariffsTab"]')?.click();
                }, 2000);
            }
        }
    });
}

function downgradeTariff() {
    tg.showAlert("Для понижения тарифа, пожалуйста, используйте команду /tariffs в чате с ботом. Это необходимо для отображения важных предупреждений о возможных ограничениях.");
}

// Делегирование событий
document.addEventListener('click', (e) => {
    if (e.target.matches('.js-purchase-tariff')) {
        purchaseTariff(e.target.dataset.id);
    }
    if (e.target.matches('.js-downgrade-tariff')) {
        downgradeTariff();
    }
});