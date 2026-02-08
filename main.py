import os
import logging
import tempfile
import time
import requests
from dotenv import load_dotenv
from telegram import Update, File
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from db import init_db, add_allowed, remove_allowed, list_allowed, is_allowed, get_owner

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
OWNER_TG_ID = int(os.getenv('OWNER_TG_ID', '0'))
SC_API_KEY = os.getenv('SC_API_KEY')
SC_USER_ID = os.getenv('SC_USER_ID')

if not BOT_TOKEN or not OWNER_TG_ID or not SC_API_KEY or not SC_USER_ID:
    print('Missing required env vars. See .env.example')
    exit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize DB (idempotent)
init_db(OWNER_TG_ID)

# SafeCheck API base URL
SAFECHECK_API_BASE = 'https://ru.safecheck.online/api'

def get_safecheck_headers():
    """Return headers for SafeCheck API authentication."""
    return {
        'SC-API-KEY': SC_API_KEY,
        'SC-USER-ID': SC_USER_ID
    }


def format_safecheck_response(data: dict) -> str:
    """Return a human-readable Russian summary for SafeCheck API response."""
    lines = []
    
    # Check for error
    if data.get('error') == 1:
        lines.append(f'❌ Ошибка: {data.get("msg", "Неизвестная ошибка")}')
        return '\n'.join(lines)
    
    result = data.get('result', {})
    
    # Main verdict
    color = result.get('color')
    is_original = result.get('is_original')
    recommendation = result.get('recommendation')
    
    if color == 'white' and is_original:
        lines.append('✅ ЧЕК ПОДЛИННЫЙ')
        lines.append(f'💬 Рекомендация: {recommendation}')
    elif color in ('red', 'black') or not is_original:
        lines.append('❌ ЧЕК ПОДДЕЛЬНЫЙ')
        lines.append(f'💬 Рекомендация: {recommendation}')
    elif color == 'yellow':
        lines.append('⚠️ ЧЕК ПОДОЗРИТЕЛЬНЫЙ')
        lines.append(f'💬 Рекомендация: {recommendation}')
    elif color == 'not_supported':
        lines.append('❓ БАНК НЕ ПОДДЕРЖИВАЕТСЯ')
    else:
        lines.append(f'Статус: {color or "неизвестен"}')
    
    # Structure check
    struct_passed = result.get('struct_passed')
    struct_result = result.get('struct_result')
    if struct_passed:
        lines.append(f'✅ Структура PDF: Корректна ({struct_result})')
    else:
        lines.append(f'❌ Структура PDF: Нарушена ({struct_result})')
    
    # Device error
    if result.get('device_error'):
        lines.append('⚠️ Файл был сохранён некорректно (device_error)')
    
    # Check data
    check_data = result.get('check_data', {})
    if check_data:
        lines.append('\n🧾 Данные чека:')
        if check_data.get('sender_fio'):
            lines.append(f'  Отправитель: {check_data.get("sender_fio")}')
        if check_data.get('sender_bank'):
            lines.append(f'  Банк отправителя: {check_data.get("sender_bank")}')
        if check_data.get('recipient_fio'):
            lines.append(f'  Получатель: {check_data.get("recipient_fio")}')
        if check_data.get('recipient_bank'):
            lines.append(f'  Банк получателя: {check_data.get("recipient_bank")}')
        if check_data.get('sum'):
            lines.append(f'  Сумма: {check_data.get("sum")}')
        if check_data.get('status'):
            lines.append(f'  Статус: {check_data.get("status")}')
        if check_data.get('date'):
            lines.append(f'  Дата: {check_data.get("date")}')
    
    verifier = result.get('verifier')
    if verifier:
        lines.append(f'\n🏦 Верификатор: {verifier}')
    
    return '\n'.join(lines)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Пришлите PDF чек как файл, я проверю его через DataGrab API.\n" 
        "Владелец бота может добавлять пользователей через /allow <tg_id>"
    )

async def allow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != OWNER_TG_ID:
        await update.message.reply_text('Только владелец может добавлять пользователей.')
        return
    args = context.args
    if not args:
        await update.message.reply_text('Использование: /allow <tg_id>')
        return
    try:
        tg_id = int(args[0])
    except ValueError:
        await update.message.reply_text('tg_id должен быть числом.')
        return
    add_allowed(tg_id)
    await update.message.reply_text(f'Пользователь {tg_id} добавлен в список allowed.')

async def disallow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != OWNER_TG_ID:
        await update.message.reply_text('Только владелец может удалять пользователей.')
        return
    args = context.args
    if not args:
        await update.message.reply_text('Использование: /disallow <tg_id>')
        return
    try:
        tg_id = int(args[0])
    except ValueError:
        await update.message.reply_text('tg_id должен быть числом.')
        return
    remove_allowed(tg_id)
    await update.message.reply_text(f'Пользователь {tg_id} удален из allowed.')

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != OWNER_TG_ID:
        await update.message.reply_text('Только владелец может просматривать список.')
        return
    users = list_allowed()
    if not users:
        await update.message.reply_text('Список пуст.')
    else:
        await update.message.reply_text('Allowed users:\n' + '\n'.join(str(u) for u in users))

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_id = update.effective_user.id
    if sender_id != OWNER_TG_ID and not is_allowed(sender_id):
        await update.message.reply_text('Вам нет доступа. Обратитесь к владельцу бота.')
        return

    doc = update.message.document
    if not doc:
        await update.message.reply_text('Пожалуйста, отправьте PDF файл как документ.')
        return
    
    # Download file
    await update.message.reply_text('Получаю файл и отправляю на проверку...')
    file: File = await context.bot.get_file(doc.file_id)
    
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tf:
        file_path = tf.name
        await file.download_to_drive(file_path)
        
        try:
            # Step 1: Submit file to SafeCheck API
            headers = get_safecheck_headers()
            with open(file_path, 'rb') as f:
                files = {'file': f}
                response = requests.post(
                    f'{SAFECHECK_API_BASE}/check',
                    headers=headers,
                    files=files,
                    timeout=60
                )
            
            response.raise_for_status()
            submit_resp = response.json()
            
            if submit_resp.get('error') == 1:
                error_msg = submit_resp.get('msg', 'Unknown error')
                await update.message.reply_text(f'❌ Ошибка при отправке чека: {error_msg}')
                return
            
            file_id = submit_resp['result']['file_id']
            logger.info(f'File submitted with ID: {file_id}')
            
            # Step 2: Poll for check result (with retries)
            max_retries = 30
            retry_delay = 2  # seconds
            check_result = None
            
            for attempt in range(max_retries):
                time.sleep(retry_delay)
                
                response = requests.get(
                    f'{SAFECHECK_API_BASE}/getCheck?file_id={file_id}',
                    headers=headers,
                    timeout=60
                )
                response.raise_for_status()
                check_result = response.json()
                
                if check_result.get('error') == 1:
                    # Permanent error
                    error_msg = check_result.get('msg', 'Unknown error')
                    await update.message.reply_text(f'❌ Ошибка при получении результата: {error_msg}')
                    return
                
                status = check_result['result'].get('status')
                if status == 'completed':
                    logger.info(f'Check completed for file_id: {file_id}')
                    break
                else:
                    logger.info(f'Check status: {status}, attempt {attempt + 1}/{max_retries}')
            
            if check_result is None or check_result['result'].get('status') != 'completed':
                await update.message.reply_text('⏱️ Проверка заняла слишком много времени. Попробуйте позже.')
                return
            
            # Step 3: Format and send result
            summary = format_safecheck_response(check_result)
            await update.message.reply_text(summary)
            
            # Send raw JSON for reference
            import json
            raw = json.dumps(check_result, ensure_ascii=False, indent=2)
            if len(raw) > 3900:
                raw = raw[:3900] + '\n... (truncated)'
            await update.message.reply_text(f'Полный ответ:\n<pre>{raw}</pre>', parse_mode='HTML')
        
        except Exception as e:
            logger.exception('Error during SafeCheck API request')
            await update.message.reply_text(f'❌ Ошибка при проверке чека: {str(e)}')
        
        finally:
            # Clean up temp file
            try:
                os.remove(file_path)
            except:
                pass

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Неизвестная команда или сообщение. Отправьте PDF файл.')

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('allow', allow_cmd))
    app.add_handler(CommandHandler('disallow', disallow_cmd))
    app.add_handler(CommandHandler('list', list_cmd))

    # document handler
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # fallback
    app.add_handler(MessageHandler(filters.ALL, unknown))

    print('Bot started...')
    app.run_polling()

if __name__ == '__main__':
    main()
