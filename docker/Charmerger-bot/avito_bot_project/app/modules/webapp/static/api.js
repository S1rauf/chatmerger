import { showLoading } from './ui.js';

const tg = window.Telegram.WebApp;

/**
 * Универсальная функция для выполнения API-запросов к бэкенду.
 * Автоматически добавляет префикс WebApp и заголовок аутентификации.
 * 
 * @param {string} endpoint - Путь к API, который должен начинаться с /api, например, '/api/main-status'.
 * @param {string} [method='GET'] - HTTP-метод (GET, POST, PUT, DELETE).
 * @param {object|null} [body=null] - Тело запроса для POST/PUT.
 * @returns {Promise<any|null>} - Результат запроса в виде JSON или null в случае ошибки.
 */
export async function apiCall(endpoint, method = 'GET', body = null) {
    // Проверяем наличие initData перед каждым запросом
    if (!tg.initData) {
        console.error("Telegram.WebApp.initData отсутствует. API вызов отменен.");
        if (tg.showAlert) {
            tg.showAlert("Ошибка аутентификации. Пожалуйста, перезапустите WebApp, полностью закрыв и открыв его заново.");
        }
        return null;
    }

    // Показываем индикатор загрузки
    showLoading(true);

    const headers = { 
        'Content-Type': 'application/json',
        'X-Telegram-Init-Data': tg.initData 
    };
    
    // Проверяем, определен ли префикс пути
    if (typeof window.WebAppPathPrefix === 'undefined') {
        console.error('Критическая ошибка: window.WebAppPathPrefix не определен!');
        if (tg.showAlert) tg.showAlert('Ошибка конфигурации WebApp (prefix).');
        showLoading(false);
        return null;
    }

    // --- ФИНАЛЬНАЯ ЛОГИКА ФОРМИРОВАНИЯ URL ---
    // Соединяем префикс WebApp ("/panel") и путь к API ("/api/...")
    // Пример: "/panel" + "/api/main-status" = "/panel/api/main-status"
    const url = `${window.WebAppPathPrefix}${endpoint}`; 
    
    // Логируем каждый вызов для удобства отладки
    console.log(`API Call: ${method} ${url}`);

    // Формируем конфигурацию для fetch
    const config = { 
        method, 
        headers 
    };

    if (body && (method === 'POST' || method === 'PUT' || method === 'DELETE')) { // DELETE тоже может иметь тело
        config.body = JSON.stringify(body);
    }

    try {
        const response = await fetch(url, config);
        
        // Обработка ошибок HTTP
        if (!response.ok) {
            let errorData = { detail: `HTTP ${response.status}: ${response.statusText}` };
            try { 
                errorData = await response.json(); 
            } catch (e) { /* Игнорируем, если тело ответа не JSON */ }
            
            console.error('API Error:', response.status, errorData);
            if (tg.showAlert) {
                tg.showAlert(`Ошибка API (${response.status}): ${errorData.detail || response.statusText}`);
            }
            return null;
        }

        // Обработка успешных ответов
        if (response.status === 204) { // 204 No Content
            return true; // Успех без тела ответа
        }
        
        const responseText = await response.text();
        return responseText ? JSON.parse(responseText) : true;

    } catch (error) {
        console.error('Network/Fetch Error:', error);
        if (tg.showAlert) {
            tg.showAlert(`Сетевая ошибка: ${error.message}. Проверьте ваше интернет-соединение.`);
        }
        return null;
    } finally {
        // Прячем индикатор загрузки в любом случае
        showLoading(false);
    }
}