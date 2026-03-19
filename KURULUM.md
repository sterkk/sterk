# ⚡ STERK — Kurulum Rehberi

## 1. Gereksinimler
```
pip install flask psycopg2-binary werkzeug
```

## 2. PostgreSQL Kurulumu (Windows)
1. https://www.postgresql.org/download/windows/ adresinden indir ve kur
2. Kurulum sırasında belirlediğin şifreyi not al
3. pgAdmin'i aç → Sağ tık "Databases" → Create → Database → İsim: **sterk**
   VEYA komut satırından:
   ```
   createdb -U postgres sterk
   ```

## 3. Veritabanı Bağlantı Ayarı
`app.py` içinde DB_CONFIG'i düzenle:
```python
DB_CONFIG = {
    'host':     'localhost',
    'port':     '5432',
    'dbname':   'sterk',
    'user':     'postgres',
    'password': 'BURAYA_KENDI_SIFREN',  # ← bunu değiştir
}
```

VEYA ortam değişkeni kullan:
```
set DATABASE_URL=postgresql://postgres:SIFREN@localhost:5432/sterk
```

## 4. Çalıştır
```
cd sterk
python app.py
```

Tarayıcıda aç: http://localhost:5000

## 5. İlk Kayıt = Admin
İlk kayıt olan kullanıcı otomatik admin olur.
Admin paneli: http://localhost:5000/admin

## 6. E-posta Doğrulama (Dev Modu)
Gerçek SMTP yok — doğrulama linki kayıt yanıtında `verify_token` olarak döner.
Linke git: http://localhost:5000/verify_email/TOKEN

## Notlar
- Veritabanı tabloları ilk çalıştırmada otomatik oluşur
- Uploads klasörü: sterk/static/uploads/
- Kategori kilidi: 90 gün (app.py'de CATEGORY_CHANGE_DAYS ile değiştir)
