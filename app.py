"""
╔══════════════════════════════════════════════════════════════╗
║        STERK v2 — PostgreSQL / Flask Sosyal Ağ              ║
║  Yeni: Shorts, Stories, Canlı Yayın, Genel Alan, Botlar     ║
╚══════════════════════════════════════════════════════════════╝
pip install flask psycopg2-binary werkzeug bcrypt flask-limiter
"""
import secrets, hashlib, random, json, os, re, threading, time
# .env dosyasını otomatik yükle (varsa)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv kurulu değilse geç
from datetime import datetime, timedelta
from urllib.parse import quote as url_quote
from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, send_from_directory)
from werkzeug.utils import secure_filename
import bcrypt
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    HAVE_LIMITER = True
except ImportError:
    HAVE_LIMITER = False

try:
    import psycopg2, psycopg2.extras, psycopg2.errors
    HAVE_PG = True
except ImportError:
    HAVE_PG = False

# ── APP ──────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY','sterk_v2_ultra_secret_2025')
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') == 'production'
app.jinja_env.filters['urlencode'] = url_quote

# Rate Limiter
if HAVE_LIMITER:
    limiter = Limiter(get_remote_address, app=app,
                      default_limits=["200 per minute"],
                      storage_uri="memory://")
else:
    limiter = None

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)),'static','uploads')
ALLOWED_EXT   = {'png','jpg','jpeg','gif','webp','mp4','mov','pdf'}
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024   # 64 MB

CAT_LOCK_DAYS = 90   # kategori değişim kilidi

# ── DB CONFIG ────────────────────────────────────────────────
DB = dict(
    host     = os.environ.get('DB_HOST','localhost'),
    port     = os.environ.get('DB_PORT','5432'),
    dbname   = os.environ.get('DB_NAME','sterk'),
    user     = os.environ.get('DB_USER','postgres'),
    password = os.environ.get('DB_PASSWORD',''),
)
DATABASE_URL = os.environ.get('DATABASE_URL','')

# Connection pool
_pool = None
def _get_pool():
    global _pool
    if _pool is None:
        try:
            from psycopg2 import pool as pg_pool
            if DATABASE_URL:
                _pool = pg_pool.ThreadedConnectionPool(1, 10, DATABASE_URL,
                    cursor_factory=psycopg2.extras.RealDictCursor)
            else:
                _pool = pg_pool.ThreadedConnectionPool(1, 10,
                    cursor_factory=psycopg2.extras.RealDictCursor,
                    **{k:v for k,v in DB.items() if v})
        except Exception:
            _pool = None
    return _pool

def get_db():
    if not HAVE_PG:
        raise RuntimeError("psycopg2 eksik! → pip install psycopg2-binary")
    pool = _get_pool()
    if pool:
        try:
            c = pool.getconn()
            c.autocommit = False
            return c
        except Exception:
            pass
    # Fallback: direct connection
    if DATABASE_URL:
        c = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        c = psycopg2.connect(cursor_factory=psycopg2.extras.RealDictCursor,
                             **{k:v for k,v in DB.items() if v})
    c.autocommit = False
    return c

def release_db(conn):
    """Connection'ı pool'a geri ver veya kapat."""
    pool = _get_pool()
    if pool:
        try: pool.putconn(conn)
        except Exception: conn.close()
    else:
        conn.close()

def run(conn, sql, p=(), one=False):
    """Merkezi sorgu — SELECT döndürür, diğerleri None."""
    cur = conn.cursor()
    cur.execute(sql, p)
    s = sql.strip().upper()
    if s.startswith('SELECT') or s.startswith('WITH') or ('RETURNING' in s):
        r = cur.fetchone() if one else cur.fetchall()
        cur.close(); return r
    cur.close(); return None

# ── VERİ ─────────────────────────────────────────────────────
COUNTRY_CITIES = {
    "Türkiye":["İstanbul","Ankara","İzmir","Bursa","Antalya","Adana","Konya","Gaziantep","Mersin","Kayseri","Eskişehir","Trabzon","Samsun","Diyarbakır","Malatya","Erzurum","Van","Denizli","Muğla","Edirne","Kocaeli","Tekirdağ","Manisa","Balıkesir","Kahramanmaraş"],
    "Almanya":["Berlin","Hamburg","Münih","Köln","Frankfurt","Stuttgart","Düsseldorf","Dortmund","Essen","Leipzig"],
    "Fransa":["Paris","Marsilya","Lyon","Toulouse","Bordeaux","Lille","Nantes","Strasbourg"],
    "İngiltere":["Londra","Birmingham","Manchester","Leeds","Liverpool","Sheffield","Bristol","Edinburgh"],
    "ABD":["New York","Los Angeles","Chicago","Houston","Phoenix","San Francisco","Seattle","Boston","Atlanta","Miami"],
    "Kanada":["Toronto","Vancouver","Montreal","Calgary","Ottawa","Edmonton"],
    "Avustralya":["Sydney","Melbourne","Brisbane","Perth","Adelaide","Canberra"],
    "Japonya":["Tokyo","Osaka","Yokohama","Nagoya","Sapporo","Kobe","Kyoto","Fukuoka"],
    "Çin":["Pekin","Şangay","Guangzhou","Shenzhen","Chengdu","Tianjin","Wuhan"],
    "Hindistan":["Mumbai","Delhi","Bangalore","Hyderabad","Chennai","Kolkata","Pune"],
    "Brezilya":["São Paulo","Rio de Janeiro","Brasília","Salvador","Fortaleza"],
    "İtalya":["Roma","Milano","Napoli","Torino","Palermo","Bologna","Floransa"],
    "İspanya":["Madrid","Barselona","Valencia","Sevilla","Zaragoza","Málaga"],
    "Hollanda":["Amsterdam","Rotterdam","Den Haag","Utrecht","Eindhoven"],
    "İsveç":["Stockholm","Göteborg","Malmö","Uppsala"],
    "Rusya":["Moskova","St. Petersburg","Novosibirsk","Yekaterinburg","Kazan"],
    "Suudi Arabistan":["Riyad","Cidde","Mekke","Medine","Dammam"],
    "BAE":["Dubai","Abu Dhabi","Sharjah","Ajman"],
    "Güney Kore":["Seul","Busan","Incheon","Daegu","Suwon"],
    "Meksika":["Mexico City","Guadalajara","Monterrey","Puebla"],
    "Polonya":["Varşova","Kraków","Wrocław","Poznań","Gdańsk"],
    "Belçika":["Brüksel","Anvers","Gent","Liège"],
    "İsviçre":["Zürih","Cenevre","Basel","Bern"],
    "Avusturya":["Viyana","Graz","Linz","Salzburg"],
    "Yunanistan":["Atina","Selanik","Patras"],
    "Pakistan":["Karaçi","Lahor","İslamabad","Faisalabad"],
    "Mısır":["Kahire","İskenderiye","Giza"],
    "Nijerya":["Lagos","Kano","Abuja"],
    "Güney Afrika":["Johannesburg","Cape Town","Durban","Pretoria"],
    "Fas":["Kazablanka","Rabat","Fes","Marakeş"],
    "Endonezya":["Jakarta","Surabaya","Bandung","Medan"],
    "Tayland":["Bangkok","Chiang Mai","Phuket"],
    "Singapur":["Singapur"],
    "Yeni Zelanda":["Auckland","Wellington","Christchurch"],
    "Diğer":["Diğer Şehir"],
}

PROFESSIONS = {
    "Eğitim & Öğretmenler":{"icon":"🎓","subs":["Matematik Öğretmeni","Fizik Öğretmeni","Kimya Öğretmeni","Biyoloji Öğretmeni","Türkçe Öğretmeni","İngilizce Öğretmeni","Tarih Öğretmeni","Müzik Öğretmeni","Sınıf Öğretmeni","Okul Öncesi Öğretmeni","Özel Eğitim Öğretmeni","Okul Müdürü","Rehber Öğretmen"]},
    "Öğrenciler":{"icon":"📚","subs":["Lise Öğrencisi","Üniversite 1. Sınıf","Üniversite 2. Sınıf","Üniversite 3. Sınıf","Üniversite 4. Sınıf","Yüksek Lisans","Doktora","Tıp Öğrencisi","Hukuk Öğrencisi","Mühendislik Öğrencisi"]},
    "Sağlık & Tıp":{"icon":"🏥","subs":["Pratisyen Hekim","Kardiyolog","Nörolog","Psikiyatrist","Pediatrist","Jinekolog","Ortopedist","Dermatolog","Hemşire","Eczacı","Diş Hekimi","Fizyoterapist","Psikolog","Diyetisyen","Acil Tıp Uzmanı"]},
    "Teknoloji & Yazılım":{"icon":"💻","subs":["Backend Developer","Frontend Developer","Full Stack Developer","Mobil Geliştirici","AI/ML Mühendisi","Veri Bilimcisi","DevOps Mühendisi","Siber Güvenlik Uzmanı","Oyun Geliştirici","UI/UX Tasarımcısı","Yazılım Mimarı","QA Mühendisi"]},
    "Mühendislik":{"icon":"⚙️","subs":["İnşaat Mühendisi","Makine Mühendisi","Elektrik Mühendisi","Elektronik Mühendisi","Bilgisayar Mühendisi","Endüstri Mühendisi","Kimya Mühendisi","Çevre Mühendisi","Havacılık Mühendisi","Gıda Mühendisi"]},
    "Sanat & Tasarım":{"icon":"🎨","subs":["Grafik Tasarımcı","İç Mimar","Mimar","Fotoğrafçı","Videograf","İllüstratör","Animatör","3D Tasarımcı","Moda Tasarımcısı","Müzisyen","Film Yönetmeni","Senarist","Ressam"]},
    "İş & Finans":{"icon":"💼","subs":["Girişimci","CEO/Genel Müdür","İK Uzmanı","Pazarlama Uzmanı","Satış Uzmanı","Mali Müşavir","Bankacı","Yatırım Uzmanı","E-Ticaret Uzmanı","Muhasebeci","Proje Yöneticisi"]},
    "Hukuk":{"icon":"⚖️","subs":["Ceza Avukatı","Medeni Hukuk Avukatı","Ticaret Hukuku Avukatı","İş Hukuku Avukatı","Hakim","Savcı","Noter","Arabulucu","Stajyer Avukat"]},
    "Medya & İletişim":{"icon":"📺","subs":["Gazeteci","Editör","YouTuber/İçerik Üreticisi","Sosyal Medya Uzmanı","Halkla İlişkiler","Radyo Yayıncısı","TV Sunucusu","Podcast Yapımcısı","Yazar","Çevirmen"]},
    "Bilim & Araştırma":{"icon":"🔬","subs":["Fizikçi","Kimyager","Biyolog","Matematikçi","Astronom","Jeolog","Arkeolog","Sosyolog","Ekonomist","Akademisyen","Araştırmacı"]},
    "Spor & Fitness":{"icon":"⚽","subs":["Futbolcu","Basketbolcu","Voleybolcu","Tenisçi","Yüzücü","Atlet","Güreşçi","Jimnastikçi","Bisikletçi","Fitness Koçu","Antrenör"]},
    "Tarım & Doğa":{"icon":"🌾","subs":["Çiftçi","Ziraat Mühendisi","Veteriner","Ormancı","Balıkçı","Arıcı","Bahçıvan","Peyzaj Mimarı"]},
    "Hobiler & İlgi Alanları":{"icon":"🎮","subs":["Kitap Okuma","Müzik","Resim & El Sanatları","Yemek Yapma","Seyahat","Fotoğrafçılık","Oyun","Anime & Manga","Film & Dizi","Yoga","Satranç","Dans","Otomobil","Hayvan Severler"]},
}

# ── YARDIMCILAR ──────────────────────────────────────────────
def gen_token(): return secrets.token_urlsafe(32)

def hash_pw(p):
    """Bcrypt ile şifre hashle."""
    return bcrypt.hashpw(p.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_pw(p, hashed):
    """Şifre doğrulama — bcrypt ve eski SHA-256 desteği."""
    if not hashed:
        return False
    # Eski SHA-256 hash'ler (64 karakter hex)
    if len(hashed) == 64 and not hashed.startswith('$2'):
        return hashlib.sha256(p.encode()).hexdigest() == hashed
    # Yeni bcrypt hash'ler
    try:
        return bcrypt.checkpw(p.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False

def ok_file(f): return '.' in f and f.rsplit('.',1)[1].lower() in ALLOWED_EXT

def get_user(uid, conn=None):
    if not uid: return None
    if conn:
        return run(conn,'SELECT * FROM users WHERE id=%s',(uid,),one=True)
    c = get_db()
    try: return run(c,'SELECT * FROM users WHERE id=%s',(uid,),one=True)
    finally: release_db(c)

def av_letter(u):
    if not u: return '?'
    n = (u.get('full_name') or u.get('username') or '?')
    return n[0].upper()

def time_ago(dt):
    if not dt: return ''
    try:
        if isinstance(dt,str): dt = datetime.strptime(dt[:19],'%Y-%m-%d %H:%M:%S')
        elif hasattr(dt,'tzinfo') and dt.tzinfo: dt = dt.replace(tzinfo=None)
        d = datetime.now()-dt; s=d.total_seconds()
        if s<60: return f"{int(s)}s"
        if s<3600: return f"{int(s//60)}dk"
        if s<86400: return f"{int(s//3600)}sa"
        if d.days<7: return f"{d.days}g"
        return str(dt)[:10]
    except Exception: return str(dt)[:10] if dt else ''

def notif(to, by, typ, ref=None, conn=None):
    """Bildirim gönder. conn verilirse o bağlantıyı kullanır (commit etmez)."""
    if to==by: return
    if conn:
        try:
            run(conn,'INSERT INTO notifications(user_id,actor_id,type,ref_id) VALUES(%s,%s,%s,%s)',(to,by,typ,ref))
        except Exception:
            pass  # duplicate veya FK hatası olabilir
        return
    c=get_db()
    try:
        run(c,'INSERT INTO notifications(user_id,actor_id,type,ref_id) VALUES(%s,%s,%s,%s)',(to,by,typ,ref))
        c.commit()
    except Exception: c.rollback()
    finally: release_db(c)

def save_tags(pid, text, c):
    for t in re.findall(r'#(\w+)',text or ''):
        tl=t.lower()
        try:
            ex=run(c,'SELECT id FROM hashtags WHERE tag=%s',(tl,),one=True)
            if ex:
                run(c,'UPDATE hashtags SET post_count=post_count+1 WHERE tag=%s',(tl,))
                hid=ex['id']
            else:
                run(c,'INSERT INTO hashtags(tag,post_count) VALUES(%s,1)',(tl,))
                hid=run(c,'SELECT id FROM hashtags WHERE tag=%s',(tl,),one=True)['id']
            # savepoint ile duplicate koruması
            cur2=c.cursor(); cur2.execute('SAVEPOINT sp_tag'); cur2.close()
            try:
                run(c,'INSERT INTO post_hashtags(post_id,hashtag_id) VALUES(%s,%s)',(pid,hid))
                cur2=c.cursor(); cur2.execute('RELEASE SAVEPOINT sp_tag'); cur2.close()
            except Exception:
                cur2=c.cursor(); cur2.execute('ROLLBACK TO SAVEPOINT sp_tag'); cur2.close()
        except Exception:
            pass  # hashtag hatası post'u engellemesin

def cat_lock(uid,c):
    r=run(c,'SELECT changed_at FROM category_changes WHERE user_id=%s ORDER BY changed_at DESC LIMIT 1',(uid,),one=True)
    if not r: return True,0,None
    last=r['changed_at']
    if isinstance(last,str): last=datetime.strptime(last[:19],'%Y-%m-%d %H:%M:%S')
    elif hasattr(last,'tzinfo') and last.tzinfo: last=last.replace(tzinfo=None)
    diff=(datetime.now()-last).days
    if diff>=CAT_LOCK_DAYS: return True,0,str(r['changed_at'])[:10]
    return False,CAT_LOCK_DAYS-diff,str(r['changed_at'])[:10]

def unread_n(uid, conn=None):
    if conn:
        r=run(conn,'SELECT COUNT(*) as n FROM notifications WHERE user_id=%s AND is_read=false',(uid,),one=True)
        return r['n'] if r else 0
    c=get_db()
    try: r=run(c,'SELECT COUNT(*) as n FROM notifications WHERE user_id=%s AND is_read=false',(uid,),one=True); return r['n'] if r else 0
    finally: release_db(c)

def unread_m(uid, conn=None):
    if conn:
        r=run(conn,'SELECT COUNT(*) as n FROM messages WHERE receiver_id=%s AND is_read=false AND is_deleted=false',(uid,),one=True)
        return r['n'] if r else 0
    c=get_db()
    try: r=run(c,'SELECT COUNT(*) as n FROM messages WHERE receiver_id=%s AND is_read=false AND is_deleted=false',(uid,),one=True); return r['n'] if r else 0
    finally: release_db(c)

def needs_mod(uid,c):
    u=run(c,'SELECT email_verified FROM users WHERE id=%s',(uid,),one=True)
    return (not u) or (not u['email_verified'])

def save_file(file, subdir):
    ext=file.filename.rsplit('.',1)[1].lower()
    fn=f"{int(datetime.now().timestamp()*1000)}_{secrets.token_hex(4)}.{ext}"
    d=os.path.join(UPLOAD_FOLDER,subdir); os.makedirs(d,exist_ok=True)
    file.save(os.path.join(d,fn))
    return f"{subdir}/{fn}", ext

# ── ŞEMA ─────────────────────────────────────────────────────
SCHEMA="""
CREATE TABLE IF NOT EXISTS users(
  id SERIAL PRIMARY KEY, username VARCHAR(30) UNIQUE NOT NULL,
  email VARCHAR(255) UNIQUE NOT NULL, password_hash VARCHAR(255) NOT NULL,
  full_name VARCHAR(100), age INTEGER, gender VARCHAR(30),
  bio TEXT, avatar VARCHAR(255), cover_photo VARCHAR(255),
  avatar_color VARCHAR(20) DEFAULT '#7c3aed',
  website VARCHAR(255), location VARCHAR(100), phone VARCHAR(30),
  is_private BOOLEAN DEFAULT false, is_verified BOOLEAN DEFAULT false,
  is_online BOOLEAN DEFAULT false, last_seen TIMESTAMP,
  post_count INTEGER DEFAULT 0, xp INTEGER DEFAULT 0,
  email_verified BOOLEAN DEFAULT false,
  is_admin BOOLEAN DEFAULT false, is_banned BOOLEAN DEFAULT false,
  is_bot BOOLEAN DEFAULT false,
  created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS user_tags(
  id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  category VARCHAR(100), subcategory VARCHAR(100),
  country VARCHAR(100), city VARCHAR(100)
);
CREATE TABLE IF NOT EXISTS category_changes(
  id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  old_category TEXT, new_category TEXT, changed_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS user_links(
  id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  platform VARCHAR(50), url VARCHAR(500)
);
CREATE TABLE IF NOT EXISTS education(
  id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  school VARCHAR(200), degree VARCHAR(100), field VARCHAR(100),
  start_year INTEGER, end_year INTEGER
);
CREATE TABLE IF NOT EXISTS experience(
  id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  company VARCHAR(200), position VARCHAR(100),
  start_year INTEGER, end_year INTEGER, is_current BOOLEAN DEFAULT false
);
CREATE TABLE IF NOT EXISTS certificates(
  id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  name VARCHAR(200), issuer VARCHAR(200), year INTEGER, file_url VARCHAR(500)
);
CREATE TABLE IF NOT EXISTS posts(
  id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  content TEXT, media_url VARCHAR(500), media_type VARCHAR(20),
  post_type VARCHAR(20) DEFAULT 'post',
  repost_of INTEGER, quote_content TEXT,
  target_category VARCHAR(100),
  visibility VARCHAR(20) DEFAULT 'public',
  is_approved BOOLEAN DEFAULT true,
  created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS shorts(
  id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  video_url VARCHAR(500) NOT NULL,
  thumbnail_url VARCHAR(500),
  caption TEXT, duration INTEGER,
  view_count INTEGER DEFAULT 0,
  created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS short_likes(
  short_id INTEGER REFERENCES shorts(id) ON DELETE CASCADE,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY(short_id,user_id)
);
CREATE TABLE IF NOT EXISTS short_comments(
  id SERIAL PRIMARY KEY,
  short_id INTEGER REFERENCES shorts(id) ON DELETE CASCADE,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  content TEXT, created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS stories(
  id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  content TEXT, bg_color VARCHAR(20),
  media_url VARCHAR(500), media_type VARCHAR(20),
  expires_at TIMESTAMP, created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS story_views(
  story_id INTEGER REFERENCES stories(id) ON DELETE CASCADE,
  viewer_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY(story_id,viewer_id)
);
CREATE TABLE IF NOT EXISTS live_streams(
  id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  title VARCHAR(200), description TEXT,
  stream_key VARCHAR(100) UNIQUE,
  is_live BOOLEAN DEFAULT false,
  viewer_count INTEGER DEFAULT 0,
  started_at TIMESTAMP, ended_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS live_viewers(
  stream_id INTEGER REFERENCES live_streams(id) ON DELETE CASCADE,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  joined_at TIMESTAMP DEFAULT NOW(),
  PRIMARY KEY(stream_id,user_id)
);
CREATE TABLE IF NOT EXISTS live_chat(
  id SERIAL PRIMARY KEY,
  stream_id INTEGER REFERENCES live_streams(id) ON DELETE CASCADE,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  message TEXT, created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS likes(
  id SERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE,
  UNIQUE(user_id,post_id)
);
CREATE TABLE IF NOT EXISTS follows(
  id SERIAL PRIMARY KEY,
  follower_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  followed_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  status VARCHAR(20) DEFAULT 'active',
  created_at TIMESTAMP DEFAULT NOW(),
  UNIQUE(follower_id,followed_id)
);
CREATE TABLE IF NOT EXISTS comments(
  id SERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE,
  content TEXT NOT NULL, parent_id INTEGER,
  created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS bookmarks(
  id SERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE,
  created_at TIMESTAMP DEFAULT NOW(),
  UNIQUE(user_id,post_id)
);
CREATE TABLE IF NOT EXISTS messages(
  id SERIAL PRIMARY KEY,
  sender_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  receiver_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  content TEXT, media_url VARCHAR(500),
  is_read BOOLEAN DEFAULT false, is_deleted BOOLEAN DEFAULT false,
  created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS groups(
  id SERIAL PRIMARY KEY, name VARCHAR(100), description TEXT,
  avatar VARCHAR(255), cover VARCHAR(255), category VARCHAR(100),
  is_private BOOLEAN DEFAULT false,
  owner_id INTEGER REFERENCES users(id),
  created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS group_members(
  id SERIAL PRIMARY KEY,
  group_id INTEGER REFERENCES groups(id) ON DELETE CASCADE,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  role VARCHAR(20) DEFAULT 'member', joined_at TIMESTAMP DEFAULT NOW(),
  UNIQUE(group_id,user_id)
);
CREATE TABLE IF NOT EXISTS group_posts(
  id SERIAL PRIMARY KEY,
  group_id INTEGER REFERENCES groups(id) ON DELETE CASCADE,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  content TEXT, media_url VARCHAR(500),
  created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS notifications(
  id SERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  actor_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  type VARCHAR(50), ref_id INTEGER,
  is_read BOOLEAN DEFAULT false, created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS hashtags(
  id SERIAL PRIMARY KEY, tag VARCHAR(100) UNIQUE,
  post_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS post_hashtags(
  post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE,
  hashtag_id INTEGER REFERENCES hashtags(id) ON DELETE CASCADE,
  PRIMARY KEY(post_id, hashtag_id)
);
CREATE TABLE IF NOT EXISTS events(
  id SERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  title VARCHAR(200), description TEXT,
  location VARCHAR(200), event_date DATE, event_time VARCHAR(10),
  cover VARCHAR(255), created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS event_attendees(
  event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  status VARCHAR(20) DEFAULT 'going', PRIMARY KEY(event_id,user_id)
);
CREATE TABLE IF NOT EXISTS jobs(
  id SERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  title VARCHAR(200), company VARCHAR(200),
  location VARCHAR(200), job_type VARCHAR(50),
  description TEXT, salary VARCHAR(100),
  is_active BOOLEAN DEFAULT true, created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS job_applications(
  id SERIAL PRIMARY KEY,
  job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  cover_letter TEXT, created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS user_settings(
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  email_notifs BOOLEAN DEFAULT true, push_notifs BOOLEAN DEFAULT true,
  show_online BOOLEAN DEFAULT true, theme VARCHAR(20) DEFAULT 'dark'
);
CREATE TABLE IF NOT EXISTS blocks(
  blocker_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  blocked_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY(blocker_id,blocked_id)
);
CREATE TABLE IF NOT EXISTS reports(
  id SERIAL PRIMARY KEY,
  reporter_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  target_type VARCHAR(20), target_id INTEGER,
  reason TEXT, status VARCHAR(20) DEFAULT 'pending',
  created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS profile_views(
  id SERIAL PRIMARY KEY,
  viewer_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  viewed_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  viewed_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS saved_jobs(
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
  UNIQUE(user_id,job_id)
);
CREATE TABLE IF NOT EXISTS questions(
  id SERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  title VARCHAR(500), content TEXT, category VARCHAR(100),
  tags TEXT, views INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS answers(
  id SERIAL PRIMARY KEY,
  question_id INTEGER REFERENCES questions(id) ON DELETE CASCADE,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  content TEXT, is_accepted BOOLEAN DEFAULT false,
  created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS email_verifications(
  id SERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  token VARCHAR(100), created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS feedback(
  id SERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  feedback_type VARCHAR(20) NOT NULL,
  subject VARCHAR(200),
  content TEXT NOT NULL,
  status VARCHAR(20) DEFAULT 'open',
  admin_reply TEXT,
  created_at TIMESTAMP DEFAULT NOW()
);
"""

def init_db():
    c=get_db()
    try:
        c.cursor().execute(SCHEMA)
        # xp sütunu yoksa ekle (mevcut DB'ler için)
        try:
            c.cursor().execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS xp INTEGER DEFAULT 0")
        except Exception:
            pass
        # post_hashtags'e PK yoksa ekle (mevcut DB'ler için)
        try:
            c.cursor().execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                        WHERE conname = 'post_hashtags_pkey') THEN
                        ALTER TABLE post_hashtags ADD PRIMARY KEY (post_id, hashtag_id);
                    END IF;
                END $$;
            """)
        except Exception:
            pass
        # password_hash sütunu bcrypt için genişlet (mevcut DB'ler için)
        try:
            c.cursor().execute("ALTER TABLE users ALTER COLUMN password_hash TYPE VARCHAR(255)")
        except Exception:
            pass
        c.commit()
        print("✅ DB şeması hazır")
    except Exception as e:
        c.rollback(); raise e
    finally: release_db(c)

# ── CONTEXT ──────────────────────────────────────────────────
@app.context_processor
def ctx():
    un=um=0; cu=None
    if 'user_id' in session:
        uid=session['user_id']
        try:
            c=get_db()
            try:
                un=unread_n(uid, conn=c)
                um=unread_m(uid, conn=c)
                cu=get_user(uid, conn=c)
            finally:
                release_db(c)
        except Exception:
            pass  # DB hatası olsa bile sayfa yüklensin
    return dict(
        get_avatar_letter=av_letter, time_ago=time_ago,
        unread_notifs=un, unread_msgs=um,
        current_user_obj=cu,
        professions=PROFESSIONS, categories=PROFESSIONS,
        country_cities=COUNTRY_CITIES,
        change_days=CAT_LOCK_DAYS,
    )

# ── GÜVENLİK HEADER'LARI ───────────────────────────────────
@app.after_request
def security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    if os.environ.get('FLASK_ENV') == 'production':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response

@app.route('/uploads/<path:fn>')
def uploaded(fn): return send_from_directory(UPLOAD_FOLDER,fn)

# ── AUTH ─────────────────────────────────────────────────────
@app.route('/')
def index():
    return redirect(url_for('feed')) if 'user_id' in session else render_template('landing.html')

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method=='POST':
        d = request.get_json(silent=True) or request.form
        un  = (d.get('username') or '').strip()
        em  = (d.get('email') or '').strip().lower()
        pw  = (d.get('password') or '')
        fn  = (d.get('full_name') or '').strip()
        age_raw = d.get('age','')
        gdr = (d.get('gender') or '').strip()

        errs=[]
        if not un: errs.append('Kullanıcı adı zorunlu')
        elif not re.match(r'^[a-zA-Z0-9_]{3,30}$',un): errs.append('Kullanıcı adı 3-30 karakter, harf/rakam/_')
        if not em or '@' not in em: errs.append('Geçerli e-posta girin')
        if len(pw)<8: errs.append('Şifre en az 8 karakter')
        if not fn: errs.append('Ad soyad zorunlu')
        try:
            age=int(str(age_raw).strip())
            if age<13 or age>100: errs.append('Geçerli yaş girin (13-100)')
        except: errs.append('Geçerli yaş girin')
        if not gdr: errs.append('Cinsiyet seçimi zorunlu')
        if errs: return jsonify({'error':' | '.join(errs)}),400

        color=random.choice(['#7c3aed','#a855f7','#6366f1','#ec4899','#f59e0b','#10b981','#3b82f6','#14b8a6','#f97316'])
        try:
            c=get_db()
        except Exception as dbe:
            return jsonify({'error':f'Veritabanı bağlantı hatası. Lütfen yönetici ile iletişime geçin. ({str(dbe)[:80]})'}),503
        try:
            run(c,'''INSERT INTO users(username,email,password_hash,full_name,age,gender,avatar_color)
                     VALUES(%s,%s,%s,%s,%s,%s,%s)''',(un,em,hash_pw(pw),fn,age,gdr,color))
            u=run(c,'SELECT * FROM users WHERE username=%s',(un,),one=True)
            if not u:
                c.rollback()
                return jsonify({'error':'Kayıt sırasında hata oluştu, lütfen tekrar deneyin.'}),500
            run(c,'INSERT INTO user_settings(user_id) VALUES(%s) ON CONFLICT DO NOTHING',(u['id'],))
            # İlk kullanıcı admin olsun
            cnt=run(c,'SELECT COUNT(*) as n FROM users',one=True)['n']
            if cnt==1: run(c,'UPDATE users SET is_admin=true WHERE id=%s',(u['id'],))
            # Email doğrulama token'ı oluştur
            tok=gen_token()
            run(c,'INSERT INTO email_verifications(user_id,token) VALUES(%s,%s)',(u['id'],tok))
            c.commit()
            # Session'ı temizle ve yeniden kur
            session.clear()
            session.permanent = True
            session['user_id']=u['id']
            session['username']=u['username']
            session['avatar_color']=u['avatar_color']
            session['avatar_letter']=av_letter(u)
            return jsonify({'success':True,'redirect':url_for('profile_setup')})
        except Exception as e:
            c.rollback()
            msg=str(e)
            if 'unique' in msg.lower() or 'duplicate' in msg.lower() or 'UniqueViolation' in type(e).__name__:
                return jsonify({'error':'Bu kullanıcı adı veya e-posta zaten kullanılıyor'}),400
            return jsonify({'error':f'Kayıt hatası: {msg}'}),500
        finally: release_db(c)
    return render_template('auth.html',mode='register')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method=='POST':
        d = request.get_json(silent=True) or request.form
        ident=(d.get('username') or '').strip()
        pw=(d.get('password') or '')
        if not ident or not pw:
            return jsonify({'error':'Kullanıcı adı ve şifre zorunlu'}),400
        try:
            c=get_db()
        except Exception as dbe:
            return jsonify({'error':f'Veritabanı bağlantı hatası: {str(dbe)[:80]}'}),503
        try:
            u=run(c,'''SELECT * FROM users
                       WHERE LOWER(username)=LOWER(%s) OR LOWER(email)=LOWER(%s)''',(ident,ident),one=True)
            if not u or not verify_pw(pw, u.get('password_hash','')):
                return jsonify({'error':'Kullanıcı adı/e-posta veya şifre hatalı'}),401
            if u['is_banned']:
                return jsonify({'error':'Bu hesap askıya alınmıştır. Destek için iletişime geçin.'}),403
            # Eski SHA-256 hash'i bcrypt'e otomatik yükselt
            if len(u.get('password_hash','')) == 64 and not u['password_hash'].startswith('$2'):
                run(c,'UPDATE users SET password_hash=%s WHERE id=%s',(hash_pw(pw),u['id']))
            run(c,'UPDATE users SET is_online=true, last_seen=NOW() WHERE id=%s',(u['id'],))
            c.commit()
            # Session'ı temizle ve kalıcı olarak kur
            session.clear()
            session.permanent = True
            session['user_id']=u['id']
            session['username']=u['username']
            session['avatar_color']=u['avatar_color']
            session['avatar_letter']=av_letter(u)
            return jsonify({'success':True,'redirect':url_for('feed')})
        except Exception as e:
            c.rollback()
            return jsonify({'error':f'Giriş hatası: {str(e)[:100]}'}),500
        finally: release_db(c)
    return render_template('auth.html',mode='login')

@app.route('/logout')
def logout():
    if 'user_id' in session:
        try:
            c=get_db()
            try:
                run(c,'UPDATE users SET is_online=false,last_seen=NOW() WHERE id=%s',(session['user_id'],))
                c.commit()
            except: c.rollback()
            finally: release_db(c)
        except: pass  # DB hatası olsa bile session temizlensin
    session.clear()
    return redirect(url_for('index'))

@app.route('/setup', methods=['GET','POST'])
def profile_setup():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method=='POST':
        d=request.get_json(silent=True) or {}
        uid=session['user_id']
        ptags=[t for t in d.get('tags',[]) if t.get('type')=='profession']
        if not ptags: return jsonify({'error':'En az bir meslek/alan kategorisi seçmelisin'}),400
        c=get_db()
        try:
            run(c,'UPDATE users SET bio=%s,location=%s,website=%s WHERE id=%s',
                (d.get('bio'),d.get('location'),d.get('website'),uid))
            run(c,'DELETE FROM user_tags WHERE user_id=%s',(uid,))
            for t in d.get('tags',[]):
                run(c,'INSERT INTO user_tags(user_id,category,subcategory,country,city) VALUES(%s,%s,%s,%s,%s)',
                    (uid,t.get('category'),t.get('subcategory'),t.get('country',''),t.get('city','')))
            run(c,'INSERT INTO category_changes(user_id,old_category,new_category) VALUES(%s,%s,%s)',
                (uid,None,','.join(t.get('category','') for t in ptags)))
            c.commit()
            return jsonify({'success':True,'redirect':url_for('feed')})
        except Exception as e:
            c.rollback(); return jsonify({'error':str(e)}),500
        finally: release_db(c)
    return render_template('setup.html')

# ── FEED ─────────────────────────────────────────────────────
@app.route('/feed')
def feed():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid=session['user_id']
    user=get_user(uid)
    if not user: session.clear(); return redirect(url_for('login'))
    tab=request.args.get('tab','for_you')  # for_you | general | following
    c=get_db()
    try:
        base_sel='''SELECT p.*,u.username,u.full_name,u.avatar_color,u.avatar,
            u.is_verified,u.is_online,
            (SELECT COUNT(*) FROM likes WHERE post_id=p.id) as like_count,
            (SELECT COUNT(*) FROM likes WHERE post_id=p.id AND user_id=%s) as liked,
            (SELECT COUNT(*) FROM comments WHERE post_id=p.id) as comment_count,
            (SELECT COUNT(*) FROM bookmarks WHERE post_id=p.id AND user_id=%s) as bookmarked,
            (SELECT username FROM users WHERE id=p.repost_of) as repost_username
            FROM posts p JOIN users u ON p.user_id=u.id
            WHERE p.visibility!='private' AND p.is_approved=true'''

        if tab=='general':
            posts=run(c,base_sel+" AND (p.target_category='Genel' OR p.target_category IS NULL)"
                      " ORDER BY p.created_at DESC LIMIT 40",(uid,uid))
        elif tab=='following':
            posts=run(c,base_sel+
                      " AND p.user_id IN (SELECT followed_id FROM follows WHERE follower_id=%s AND status='active')"
                      " ORDER BY p.created_at DESC LIMIT 40",(uid,uid,uid))
        else:  # for_you — kendi + takip + genel
            posts=run(c,base_sel+
                      " AND (p.user_id=%s"
                      "  OR p.user_id IN (SELECT followed_id FROM follows WHERE follower_id=%s AND status='active')"
                      "  OR p.target_category='Genel')"
                      " ORDER BY p.created_at DESC LIMIT 40",(uid,uid,uid,uid))

        stories=run(c,'''SELECT s.*,u.username,u.full_name,u.avatar_color,u.avatar,
            (SELECT COUNT(*)>0 FROM story_views WHERE story_id=s.id AND viewer_id=%s) as viewed
            FROM stories s JOIN users u ON s.user_id=u.id
            WHERE s.expires_at>NOW()
            AND (s.user_id=%s OR s.user_id IN(SELECT followed_id FROM follows WHERE follower_id=%s AND status='active'))
            ORDER BY s.created_at DESC LIMIT 20''',(uid,uid,uid))

        suggested=run(c,'''SELECT DISTINCT u.*,
            (SELECT COUNT(*) FROM follows WHERE followed_id=u.id AND status='active') as follower_count,
            (SELECT COUNT(*) FROM user_tags WHERE user_id=u.id AND subcategory IN
                (SELECT subcategory FROM user_tags WHERE user_id=%s)) as common_tags
            FROM users u
            WHERE u.id!=%s AND u.is_banned=false
            AND u.id NOT IN(SELECT followed_id FROM follows WHERE follower_id=%s AND status='active')
            AND u.id NOT IN(SELECT blocked_id FROM blocks WHERE blocker_id=%s)
            ORDER BY common_tags DESC,follower_count DESC LIMIT 6''',(uid,uid,uid,uid))

        trending=run(c,'SELECT tag,post_count FROM hashtags ORDER BY post_count DESC LIMIT 10')
        events=run(c,'''SELECT e.*,u.username FROM events e JOIN users u ON e.user_id=u.id
            WHERE e.event_date>=CURRENT_DATE ORDER BY e.event_date ASC LIMIT 3''')
        live=run(c,'''SELECT l.*,u.username,u.full_name,u.avatar_color,u.avatar
            FROM live_streams l JOIN users u ON l.user_id=u.id
            WHERE l.is_live=true ORDER BY l.viewer_count DESC LIMIT 4''')
        user_cats=run(c,'SELECT DISTINCT category FROM user_tags WHERE user_id=%s',(uid,))
        ucl=[r['category'] for r in (user_cats or [])]

        return render_template('feed.html',
            posts=posts or [],user=user,stories=stories or [],
            suggested=suggested or [],trending=trending or [],
            events=events or [],live_streams=live or [],
            user_cat_list=ucl, active_tab=tab)
    finally: release_db(c)

# ── POSTS ────────────────────────────────────────────────────
@app.route('/post', methods=['POST'])
def create_post():
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    uid=session['user_id']
    content=request.form.get('content','').strip()
    vis=request.form.get('visibility','public')
    tcat=request.form.get('target_category','')

    if not content and 'file' not in request.files:
        return jsonify({'error':'İçerik veya medya gerekli'}),400
    if len(content)>1000: return jsonify({'error':'Maksimum 1000 karakter'}),400

    # Kategori erişim kontrolü
    if tcat and tcat not in ('Genel',''):
        c2=get_db()
        try:
            ucats=[r['category'] for r in (run(c2,'SELECT DISTINCT category FROM user_tags WHERE user_id=%s',(uid,)) or [])]
            if tcat not in ucats:
                return jsonify({'error':f'Bu kategoriye paylaşım yapma yetkiniz yok. Kendi kategorini veya "Genel"i seçin.'}),403
        finally: c2.close()

    media_url=None; media_type=None
    if 'file' in request.files:
        f=request.files['file']
        if f and f.filename and ok_file(f.filename):
            path,ext=save_file(f,'posts')
            media_url=path
            media_type='video' if ext in ('mp4','mov') else 'image'

    c=get_db()
    try:
        approved=not needs_mod(uid,c)
        run(c,'''INSERT INTO posts(user_id,content,media_url,media_type,visibility,target_category,is_approved)
                 VALUES(%s,%s,%s,%s,%s,%s,%s)''',(uid,content,media_url,media_type,vis,tcat or None,approved))
        pid=run(c,'SELECT id FROM posts WHERE user_id=%s ORDER BY created_at DESC LIMIT 1',(uid,),one=True)['id']
        save_tags(pid,content,c)
        run(c,'UPDATE users SET post_count=post_count+1 WHERE id=%s',(uid,))
        c.commit()
        return jsonify({'success':True,'moderated':not approved})
    except Exception as e:
        c.rollback(); return jsonify({'error':str(e)}),500
    finally: release_db(c)

@app.route('/like/<int:pid>', methods=['POST'])
def like_post(pid):
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    uid=session['user_id']; c=get_db()
    try:
        ex=run(c,'SELECT 1 FROM likes WHERE user_id=%s AND post_id=%s',(uid,pid),one=True)
        post=run(c,'SELECT user_id FROM posts WHERE id=%s',(pid,),one=True)
        if ex:
            run(c,'DELETE FROM likes WHERE user_id=%s AND post_id=%s',(uid,pid)); liked=False
        else:
            run(c,'INSERT INTO likes(user_id,post_id) VALUES(%s,%s)',(uid,pid)); liked=True
            if post: notif(post['user_id'],uid,'like',pid)
        cnt=run(c,'SELECT COUNT(*) as n FROM likes WHERE post_id=%s',(pid,),one=True)['n']
        c.commit(); return jsonify({'liked':liked,'count':cnt})
    finally: release_db(c)

@app.route('/bookmark/<int:pid>', methods=['POST'])
def bookmark(pid):
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    uid=session['user_id']; c=get_db()
    try:
        ex=run(c,'SELECT 1 FROM bookmarks WHERE user_id=%s AND post_id=%s',(uid,pid),one=True)
        if ex: run(c,'DELETE FROM bookmarks WHERE user_id=%s AND post_id=%s',(uid,pid)); saved=False
        else: run(c,'INSERT INTO bookmarks(user_id,post_id) VALUES(%s,%s)',(uid,pid)); saved=True
        c.commit(); return jsonify({'saved':saved})
    finally: release_db(c)

@app.route('/comment/<int:pid>', methods=['POST'])
def add_comment(pid):
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    d=request.get_json(silent=True) or {}; uid=session['user_id']
    txt=(d.get('content') or '').strip()
    if not txt: return jsonify({'error':'Yorum boş olamaz'}),400
    c=get_db()
    try:
        post=run(c,'SELECT user_id FROM posts WHERE id=%s',(pid,),one=True)
        run(c,'INSERT INTO comments(user_id,post_id,content) VALUES(%s,%s,%s)',(uid,pid,txt))
        if post: notif(post['user_id'],uid,'comment',pid)
        c.commit()
        u=get_user(uid)
        return jsonify({'success':True,'comment':{'content':txt,'username':session['username'],
            'full_name':u['full_name'],'avatar_color':u['avatar_color'],'avatar':u['avatar']}})
    finally: release_db(c)

@app.route('/comments/<int:pid>')
def get_comments(pid):
    c=get_db()
    try:
        rows=run(c,'''SELECT cm.*,u.username,u.full_name,u.avatar_color,u.avatar
            FROM comments cm JOIN users u ON cm.user_id=u.id
            WHERE cm.post_id=%s ORDER BY cm.created_at ASC''',(pid,))
        return jsonify([dict(r) for r in (rows or [])])
    finally: release_db(c)

@app.route('/delete_post/<int:pid>', methods=['POST'])
def delete_post(pid):
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    uid=session['user_id']; c=get_db()
    try:
        p=run(c,'SELECT user_id FROM posts WHERE id=%s',(pid,),one=True)
        if not p or p['user_id']!=uid: return jsonify({'error':'Yetki yok'}),403
        run(c,'DELETE FROM posts WHERE id=%s',(pid,))
        run(c,'UPDATE users SET post_count=GREATEST(0,post_count-1) WHERE id=%s',(uid,))
        c.commit(); return jsonify({'success':True})
    finally: release_db(c)

@app.route('/repost/<int:pid>', methods=['POST'])
def repost(pid):
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    d=request.get_json(silent=True) or {}; uid=session['user_id']; c=get_db()
    try:
        orig=run(c,'SELECT * FROM posts WHERE id=%s',(pid,),one=True)
        if not orig: return jsonify({'error':'Bulunamadı'}),404
        q=(d.get('quote') or '').strip()
        run(c,'INSERT INTO posts(user_id,content,post_type,repost_of,quote_content) VALUES(%s,%s,%s,%s,%s)',
            (uid,q,'quote' if q else 'repost',pid,q or None))
        run(c,'UPDATE users SET post_count=post_count+1 WHERE id=%s',(uid,))
        notif(orig['user_id'],uid,'repost',pid)
        c.commit(); return jsonify({'success':True})
    finally: release_db(c)

# ── SHORTS ───────────────────────────────────────────────────
@app.route('/shorts')
def shorts():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid=session['user_id']; c=get_db()
    try:
        vids=run(c,'''SELECT s.*,u.username,u.full_name,u.avatar_color,u.avatar,u.is_verified,
            (SELECT COUNT(*) FROM short_likes WHERE short_id=s.id) as like_count,
            (SELECT COUNT(*)>0 FROM short_likes WHERE short_id=s.id AND user_id=%s) as liked,
            (SELECT COUNT(*) FROM short_comments WHERE short_id=s.id) as comment_count
            FROM shorts s JOIN users u ON s.user_id=u.id
            ORDER BY s.created_at DESC LIMIT 50''',(uid,))
        return render_template('shorts.html',shorts=vids or [],user=get_user(uid))
    except Exception as e:
        app.logger.error(f"Shorts error: {e}")
        try: c.rollback()
        except: pass
        return render_template('shorts.html',shorts=[],user=get_user(uid))
    finally: release_db(c)

@app.route('/shorts/upload', methods=['POST'])
def upload_short():
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    uid=session['user_id']
    if 'video' not in request.files: return jsonify({'error':'Video dosyası gerekli'}),400
    f=request.files['video']
    if not f or not ok_file(f.filename): return jsonify({'error':'Geçersiz video dosyası'}),400
    caption=(request.form.get('caption') or '').strip()
    path,_=save_file(f,'shorts')
    c=get_db()
    try:
        run(c,'INSERT INTO shorts(user_id,video_url,caption) VALUES(%s,%s,%s)',(uid,path,caption))
        c.commit(); return jsonify({'success':True})
    finally: release_db(c)

@app.route('/shorts/like/<int:sid>', methods=['POST'])
def like_short(sid):
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    uid=session['user_id']; c=get_db()
    try:
        ex=run(c,'SELECT 1 FROM short_likes WHERE short_id=%s AND user_id=%s',(sid,uid),one=True)
        if ex: run(c,'DELETE FROM short_likes WHERE short_id=%s AND user_id=%s',(sid,uid)); liked=False
        else: run(c,'INSERT INTO short_likes VALUES(%s,%s)',(sid,uid)); liked=True
        cnt=run(c,'SELECT COUNT(*) as n FROM short_likes WHERE short_id=%s',(sid,),one=True)['n']
        c.commit(); return jsonify({'liked':liked,'count':cnt})
    finally: release_db(c)

@app.route('/shorts/comment/<int:sid>', methods=['POST'])
def comment_short(sid):
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    d=request.get_json(silent=True) or {}; uid=session['user_id']
    txt=(d.get('content') or '').strip()
    if not txt: return jsonify({'error':'Boş olamaz'}),400
    c=get_db()
    try:
        run(c,'INSERT INTO short_comments(short_id,user_id,content) VALUES(%s,%s,%s)',(sid,uid,txt))
        c.commit(); u=get_user(uid)
        return jsonify({'success':True,'username':u['username'],'content':txt,
            'avatar_color':u['avatar_color'],'avatar':u['avatar']})
    finally: release_db(c)

@app.route('/shorts/view/<int:sid>', methods=['POST'])
def view_short(sid):
    if 'user_id' not in session: return jsonify({'ok':True})
    c=get_db()
    try:
        run(c,'UPDATE shorts SET view_count=view_count+1 WHERE id=%s',(sid,))
        c.commit(); return jsonify({'ok':True})
    finally: release_db(c)

@app.route('/shorts/<int:sid>/comments')
def short_comments(sid):
    c=get_db()
    try:
        rows=run(c,'''SELECT sc.*,u.username,u.avatar_color,u.avatar
            FROM short_comments sc JOIN users u ON sc.user_id=u.id
            WHERE sc.short_id=%s ORDER BY sc.created_at ASC LIMIT 100''',(sid,))
        return jsonify([dict(r) for r in (rows or [])])
    finally: release_db(c)

# ── STORIES ──────────────────────────────────────────────────
@app.route('/story', methods=['POST'])
def create_story():
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    uid=session['user_id']
    media_url=None; media_type=None; bg_color='#7c3aed'

    # FormData (hem dosyalı hem dosyasız)
    if request.content_type and 'multipart' in request.content_type:
        content=(request.form.get('content') or '').strip()
        bg_color=request.form.get('bg_color','#7c3aed')
        if 'file' in request.files:
            f=request.files['file']
            if f and f.filename and ok_file(f.filename):
                path,ext=save_file(f,'stories')
                media_url=path
                media_type='video' if ext in ('mp4','mov') else 'image'
    else:
        # JSON ile metin hikayesi
        d=request.get_json(silent=True) or {}
        content=(d.get('content') or '').strip()
        bg_color=d.get('bg_color','#7c3aed')

    if not content and not media_url: return jsonify({'error':'İçerik veya medya gerekli'}),400
    exp=datetime.now()+timedelta(hours=24)
    c=get_db()
    try:
        run(c,'INSERT INTO stories(user_id,content,bg_color,media_url,media_type,expires_at) VALUES(%s,%s,%s,%s,%s,%s)',
            (uid,content,bg_color,media_url,media_type,exp))
        c.commit(); return jsonify({'success':True})
    finally: release_db(c)

@app.route('/story/view/<int:sid>', methods=['POST'])
def view_story(sid):
    if 'user_id' not in session: return jsonify({'ok':True})
    c=get_db()
    try:
        try:
            run(c,'INSERT INTO story_views VALUES(%s,%s)',(sid,session['user_id']))
            c.commit()
        except: c.rollback()
        return jsonify({'ok':True})
    finally: release_db(c)

# ── LIVE STREAM ──────────────────────────────────────────────
@app.route('/live')
def live_list():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid=session['user_id']; c=get_db()
    try:
        streams=run(c,'''SELECT l.*,u.username,u.full_name,u.avatar_color,u.avatar
            FROM live_streams l JOIN users u ON l.user_id=u.id
            WHERE l.is_live=true ORDER BY l.viewer_count DESC''')
        my=run(c,'SELECT * FROM live_streams WHERE user_id=%s ORDER BY created_at DESC LIMIT 1',(uid,),one=True)
        return render_template('live.html',streams=streams or [],my_stream=my,user=get_user(uid))
    finally: release_db(c)

@app.route('/live/start', methods=['POST'])
def start_live():
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    uid=session['user_id']
    d=request.get_json(silent=True) or {}
    title=(d.get('title') or 'Canlı Yayın').strip()
    key=gen_token()[:16]
    c=get_db()
    try:
        # Var olan yayını bitir
        run(c,'UPDATE live_streams SET is_live=false,ended_at=NOW() WHERE user_id=%s AND is_live=true',(uid,))
        run(c,'''INSERT INTO live_streams(user_id,title,description,stream_key,is_live,started_at)
                 VALUES(%s,%s,%s,%s,true,NOW())''',(uid,title,d.get('description',''),key))
        sid=run(c,'SELECT id FROM live_streams WHERE user_id=%s ORDER BY created_at DESC LIMIT 1',(uid,),one=True)['id']
        c.commit()
        return jsonify({'success':True,'stream_id':sid,'stream_key':key})
    finally: release_db(c)

@app.route('/live/end', methods=['POST'])
def end_live():
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    uid=session['user_id']; c=get_db()
    try:
        run(c,'UPDATE live_streams SET is_live=false,ended_at=NOW() WHERE user_id=%s AND is_live=true',(uid,))
        c.commit(); return jsonify({'success':True})
    finally: release_db(c)

@app.route('/live/<int:sid>')
def watch_live(sid):
    if 'user_id' not in session: return redirect(url_for('login'))
    uid=session['user_id']; c=get_db()
    try:
        s=run(c,'''SELECT l.*,u.username,u.full_name,u.avatar_color,u.avatar
            FROM live_streams l JOIN users u ON l.user_id=u.id WHERE l.id=%s''',(sid,),one=True)
        if not s: return redirect(url_for('live_list'))
        try:
            run(c,'INSERT INTO live_viewers VALUES(%s,%s)',(sid,uid))
            run(c,'UPDATE live_streams SET viewer_count=viewer_count+1 WHERE id=%s',(sid,))
            c.commit()
        except: c.rollback()
        chat=run(c,'''SELECT lc.*,u.username,u.avatar_color,u.avatar
            FROM live_chat lc JOIN users u ON lc.user_id=u.id
            WHERE lc.stream_id=%s ORDER BY lc.created_at DESC LIMIT 50''',(sid,))
        return render_template('live_watch.html',stream=s,chat=list(reversed(chat or [])),user=get_user(uid))
    finally: release_db(c)

@app.route('/live/<int:sid>/chat', methods=['POST'])
def live_chat(sid):
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    d=request.get_json(silent=True) or {}; uid=session['user_id']
    msg=(d.get('message') or '').strip()
    if not msg: return jsonify({'error':'Boş mesaj'}),400
    c=get_db()
    try:
        run(c,'INSERT INTO live_chat(stream_id,user_id,message) VALUES(%s,%s,%s)',(sid,uid,msg))
        c.commit(); u=get_user(uid)
        return jsonify({'success':True,'username':u['username'],'message':msg,
            'avatar_color':u['avatar_color'],'avatar':u['avatar'],
            'time':datetime.now().strftime('%H:%M')})
    finally: release_db(c)

@app.route('/api/live/<int:sid>/messages')
def live_messages(sid):
    if 'user_id' not in session: return jsonify({'messages':[],'viewer_count':0})
    c=get_db()
    try:
        rows=run(c,'''SELECT lc.*,u.username,u.avatar_color,u.avatar
            FROM live_chat lc JOIN users u ON lc.user_id=u.id
            WHERE lc.stream_id=%s ORDER BY lc.created_at ASC LIMIT 100''',(sid,))
        stream=run(c,'SELECT viewer_count,is_live FROM live_streams WHERE id=%s',(sid,),one=True)
        return jsonify({
            'messages':[dict(r) for r in (rows or [])],
            'viewer_count':stream['viewer_count'] if stream else 0,
            'is_live':stream['is_live'] if stream else False
        })
    finally: release_db(c)

# ── PROFILE ──────────────────────────────────────────────────
@app.route('/profile/<username>')
def profile(username):
    if 'user_id' not in session: return redirect(url_for('login'))
    c=get_db()
    try:
        pu=run(c,'SELECT * FROM users WHERE username=%s',(username,),one=True)
        if not pu: return render_template('404.html'),404
        uid=session['user_id']
        if uid!=pu['id']:
            try:
                run(c,'INSERT INTO profile_views(viewer_id,viewed_id) VALUES(%s,%s)',(uid,pu['id']))
                c.commit()
            except: c.rollback()
        tab=request.args.get('tab','posts')
        bsel='''SELECT p.*,u.username,u.full_name,u.avatar_color,u.avatar,u.is_verified,
            (SELECT COUNT(*) FROM likes WHERE post_id=p.id) as like_count,
            (SELECT COUNT(*) FROM likes WHERE post_id=p.id AND user_id=%s) as liked,
            (SELECT COUNT(*) FROM comments WHERE post_id=p.id) as comment_count,
            (SELECT COUNT(*) FROM bookmarks WHERE post_id=p.id AND user_id=%s) as bookmarked
            FROM posts p JOIN users u ON p.user_id=u.id'''
        if tab=='likes':
            posts=run(c,bsel+' JOIN likes l ON l.post_id=p.id WHERE l.user_id=%s ORDER BY l.id DESC',(uid,uid,pu['id']))
        elif tab=='media':
            posts=run(c,bsel+' WHERE p.user_id=%s AND p.media_url IS NOT NULL ORDER BY p.created_at DESC',(uid,uid,pu['id']))
        else:
            posts=run(c,bsel+' WHERE p.user_id=%s ORDER BY p.created_at DESC',(uid,uid,pu['id']))

        tags=run(c,'SELECT * FROM user_tags WHERE user_id=%s',(pu['id'],))
        links=run(c,'SELECT * FROM user_links WHERE user_id=%s',(pu['id'],))
        edu=run(c,'SELECT * FROM education WHERE user_id=%s ORDER BY end_year DESC NULLS LAST',(pu['id'],))
        exp=run(c,'SELECT * FROM experience WHERE user_id=%s ORDER BY is_current DESC,end_year DESC NULLS LAST',(pu['id'],))
        certs=run(c,'SELECT * FROM certificates WHERE user_id=%s ORDER BY year DESC NULLS LAST',(pu['id'],))
        fc=run(c,"SELECT COUNT(*) as n FROM follows WHERE followed_id=%s AND status='active'",(pu['id'],),one=True)['n']
        fgc=run(c,"SELECT COUNT(*) as n FROM follows WHERE follower_id=%s AND status='active'",(pu['id'],),one=True)['n']
        isf=run(c,"SELECT 1 FROM follows WHERE follower_id=%s AND followed_id=%s AND status='active'",(uid,pu['id']),one=True)
        blk=run(c,'SELECT 1 FROM blocks WHERE blocker_id=%s AND blocked_id=%s',(uid,pu['id']),one=True)
        vc=run(c,'SELECT COUNT(*) as n FROM profile_views WHERE viewed_id=%s',(pu['id'],),one=True)['n']
        can_ch,days_l,_=cat_lock(pu['id'],c)

        # Ortak takip
        common_follows=[]
        if uid!=pu['id']:
            try:
                common_follows=run(c,'''SELECT u.username FROM follows f1
                    JOIN follows f2 ON f1.followed_id=f2.followed_id
                    JOIN users u ON u.id=f1.followed_id
                    WHERE f1.follower_id=%s AND f2.follower_id=%s AND f1.status='active' AND f2.status='active'
                    LIMIT 5''',(uid,pu['id'])) or []
            except: common_follows=[]

        badges=[]
        if pu['is_verified']: badges.append({'icon':'💎','name':'Doğrulanmış','desc':'Doğrulanmış hesap'})
        if fc>=1000: badges.append({'icon':'🌟','name':'Fenomen','desc':'1000+ takipçi'})
        elif fc>=100: badges.append({'icon':'⭐','name':'Popüler','desc':'100+ takipçi'})
        pc=run(c,'SELECT COUNT(*) as n FROM posts WHERE user_id=%s',(pu['id'],),one=True)['n']
        if pc>=50: badges.append({'icon':'🔥','name':'Aktif','desc':'50+ gönderi'})
        if pu['bio'] and pu['full_name']: badges.append({'icon':'✅','name':'Tam Profil','desc':'Profil tamamlanmış'})
        if pu.get('is_bot'): badges.append({'icon':'🤖','name':'Bot','desc':'Bot hesap'})

        return render_template('profile.html',
            profile_user=pu,tags=tags or [],posts=posts or [],
            links=links or [],education=edu or [],experience=exp or [],
            follower_count=fc,following_count=fgc,
            is_following=bool(isf),current_user=get_user(uid),
            badges=badges,blocked=bool(blk),tab=tab,
            view_count=vc,can_change_cat=can_ch,days_left=days_l,certs=certs or [],
            common_follows=common_follows)
    finally: release_db(c)

@app.route('/edit_profile', methods=['GET','POST'])
def edit_profile():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid=session['user_id']
    if request.method=='POST':
        d=request.get_json(silent=True) or {}; c=get_db()
        try:
            can_ch,days_l,_=cat_lock(uid,c)
            ntags=d.get('tags',[])
            otags=run(c,'SELECT category FROM user_tags WHERE user_id=%s',(uid,)) or []
            ocats=set(t['category'] for t in otags)
            ncats=set(t.get('category','') for t in ntags if t.get('type')=='profession')
            if ocats!=ncats and not can_ch:
                return jsonify({'error':f'Kategori değiştirmek için {days_l} gün daha beklemelisin.'}),403
            run(c,'''UPDATE users SET full_name=%s,age=%s,gender=%s,bio=%s,location=%s,
                     website=%s,phone=%s,is_private=%s WHERE id=%s''',
                (d.get('full_name'),d.get('age'),d.get('gender'),d.get('bio'),
                 d.get('location'),d.get('website'),d.get('phone'),d.get('is_private',False),uid))
            if ocats!=ncats:
                run(c,'INSERT INTO category_changes(user_id,old_category,new_category) VALUES(%s,%s,%s)',
                    (uid,','.join(ocats),','.join(ncats)))
            run(c,'DELETE FROM user_tags WHERE user_id=%s',(uid,))
            for t in ntags:
                run(c,'INSERT INTO user_tags(user_id,category,subcategory,country,city) VALUES(%s,%s,%s,%s,%s)',
                    (uid,t.get('category'),t.get('subcategory'),t.get('country',''),t.get('city','')))
            run(c,'DELETE FROM user_links WHERE user_id=%s',(uid,))
            for l in d.get('links',[]):
                run(c,'INSERT INTO user_links(user_id,platform,url) VALUES(%s,%s,%s)',(uid,l['platform'],l['url']))
            run(c,'DELETE FROM education WHERE user_id=%s',(uid,))
            for e in d.get('education',[]):
                run(c,'INSERT INTO education(user_id,school,degree,field,start_year,end_year) VALUES(%s,%s,%s,%s,%s,%s)',
                    (uid,e.get('school'),e.get('degree'),e.get('field'),e.get('start_year'),e.get('end_year')))
            run(c,'DELETE FROM experience WHERE user_id=%s',(uid,))
            for e in d.get('experience',[]):
                run(c,'INSERT INTO experience(user_id,company,position,start_year,end_year,is_current) VALUES(%s,%s,%s,%s,%s,%s)',
                    (uid,e.get('company'),e.get('position'),e.get('start_year'),e.get('end_year'),e.get('current',False)))
            u=run(c,'SELECT * FROM users WHERE id=%s',(uid,),one=True)
            session['avatar_letter']=av_letter(u)
            c.commit(); return jsonify({'success':True})
        except Exception as e:
            c.rollback(); return jsonify({'error':str(e)}),500
        finally: release_db(c)
    user=get_user(uid); c=get_db()
    try:
        tags=run(c,'SELECT * FROM user_tags WHERE user_id=%s',(uid,))
        links=run(c,'SELECT * FROM user_links WHERE user_id=%s',(uid,))
        edu=run(c,'SELECT * FROM education WHERE user_id=%s',(uid,))
        exp=run(c,'SELECT * FROM experience WHERE user_id=%s',(uid,))
        can_ch,days_l,_=cat_lock(uid,c)
        return render_template('edit_profile.html',user=user,
            tags=tags or [],links=links or [],education=edu or [],experience=exp or [],
            can_change_cat=can_ch,days_left=days_l,change_days=CAT_LOCK_DAYS)
    finally: release_db(c)

@app.route('/upload_avatar', methods=['POST'])
def upload_avatar():
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    uid=session['user_id']
    if 'file' not in request.files: return jsonify({'error':'Dosya seçilmedi'}),400
    f=request.files['file']
    if not f or not ok_file(f.filename): return jsonify({'error':'Geçersiz dosya'}),400
    path,_=save_file(f,'avatars')
    c=get_db()
    try:
        run(c,'UPDATE users SET avatar=%s WHERE id=%s',(path,uid))
        c.commit(); return jsonify({'success':True,'url':f'/uploads/{path}'})
    finally: release_db(c)

@app.route('/upload_cover', methods=['POST'])
def upload_cover():
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    uid=session['user_id']
    if 'file' not in request.files: return jsonify({'error':'Dosya seçilmedi'}),400
    f=request.files['file']
    if not f or not ok_file(f.filename): return jsonify({'error':'Geçersiz dosya'}),400
    path,_=save_file(f,'covers')
    c=get_db()
    try:
        run(c,'UPDATE users SET cover_photo=%s WHERE id=%s',(path,uid))
        c.commit(); return jsonify({'success':True,'url':f'/uploads/{path}'})
    finally: release_db(c)

# ── SOSYAL ───────────────────────────────────────────────────
@app.route('/follow/<int:target_id>', methods=['POST'])
def follow(target_id):
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    me=session['user_id']
    if me==target_id: return jsonify({'error':'Kendinizi takip edemezsiniz'}),400
    c=get_db()
    try:
        tgt=run(c,'SELECT is_private FROM users WHERE id=%s',(target_id,),one=True)
        if not tgt: return jsonify({'error':'Kullanıcı bulunamadı'}),404
        ex=run(c,'SELECT id FROM follows WHERE follower_id=%s AND followed_id=%s',(me,target_id),one=True)
        if ex:
            run(c,'DELETE FROM follows WHERE follower_id=%s AND followed_id=%s',(me,target_id))
            following=False
        else:
            st='pending' if tgt['is_private'] else 'active'
            run(c,'INSERT INTO follows(follower_id,followed_id,status) VALUES(%s,%s,%s)',(me,target_id,st))
            following=True
            if st=='active': notif(target_id,me,'follow')
        cnt=run(c,"SELECT COUNT(*) as n FROM follows WHERE followed_id=%s AND status='active'",(target_id,),one=True)['n']
        c.commit()
        return jsonify({'following':following,'count':cnt})
    finally: release_db(c)

@app.route('/block/<int:target_id>', methods=['POST'])
def block_user(target_id):
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    me=session['user_id']; c=get_db()
    try:
        ex=run(c,'SELECT 1 FROM blocks WHERE blocker_id=%s AND blocked_id=%s',(me,target_id),one=True)
        if ex: run(c,'DELETE FROM blocks WHERE blocker_id=%s AND blocked_id=%s',(me,target_id)); bl=False
        else: run(c,'INSERT INTO blocks VALUES(%s,%s)',(me,target_id)); bl=True
        c.commit(); return jsonify({'blocked':bl})
    finally: release_db(c)

@app.route('/report', methods=['POST'])
def report():
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    d=request.get_json(silent=True) or {}; uid=session['user_id']; c=get_db()
    try:
        run(c,'INSERT INTO reports(reporter_id,target_type,target_id,reason) VALUES(%s,%s,%s,%s)',
            (uid,d.get('type'),d.get('id'),d.get('reason','')))
        c.commit(); return jsonify({'success':True})
    finally: release_db(c)

# ── MESSAGES ─────────────────────────────────────────────────
@app.route('/messages')
def messages():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid=session['user_id']; c=get_db()
    try:
        convos=run(c,'''
            SELECT DISTINCT ON(oid) oid,u.username,u.full_name,u.avatar_color,u.avatar,u.is_online,
                last_msg,last_time,
                (SELECT COUNT(*) FROM messages WHERE sender_id=oid AND receiver_id=%s AND is_read=false) as unread
            FROM (
                SELECT CASE WHEN sender_id=%s THEN receiver_id ELSE sender_id END as oid,
                    content as last_msg, created_at as last_time
                FROM messages WHERE (sender_id=%s OR receiver_id=%s) AND is_deleted=false
            ) sub JOIN users u ON u.id=sub.oid
            ORDER BY oid,last_time DESC
        ''',(uid,uid,uid,uid))
        return render_template('messages.html',convos=convos or [],user=get_user(uid))
    finally: release_db(c)

@app.route('/messages/<username>')
def conversation(username):
    if 'user_id' not in session: return redirect(url_for('login'))
    uid=session['user_id']; c=get_db()
    try:
        other=run(c,'SELECT * FROM users WHERE username=%s',(username,),one=True)
        if not other: return redirect(url_for('messages'))
        msgs=run(c,'''SELECT m.*,u.username,u.full_name,u.avatar_color,u.avatar
            FROM messages m JOIN users u ON m.sender_id=u.id
            WHERE ((m.sender_id=%s AND m.receiver_id=%s) OR (m.sender_id=%s AND m.receiver_id=%s))
            AND m.is_deleted=false ORDER BY m.created_at ASC''',(uid,other['id'],other['id'],uid))
        run(c,'UPDATE messages SET is_read=true WHERE sender_id=%s AND receiver_id=%s',(other['id'],uid))
        c.commit()
        return render_template('conversation.html',msgs=msgs or [],other=other,user=get_user(uid))
    finally: release_db(c)

@app.route('/messages/send', methods=['POST'])
def send_msg():
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    d=request.get_json(silent=True) or {}; uid=session['user_id']
    rid=d.get('receiver_id'); txt=(d.get('content') or '').strip()
    if not txt: return jsonify({'error':'Mesaj boş olamaz'}),400
    c=get_db()
    try:
        run(c,'INSERT INTO messages(sender_id,receiver_id,content) VALUES(%s,%s,%s)',(uid,rid,txt))
        notif(rid,uid,'message')
        c.commit(); return jsonify({'success':True})
    finally: release_db(c)

# ── NOTIFICATIONS ────────────────────────────────────────────
@app.route('/notifications')
def notifications():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid=session['user_id']; c=get_db()
    try:
        rows=run(c,'''SELECT n.*,u.username,u.full_name,u.avatar_color,u.avatar
            FROM notifications n JOIN users u ON n.actor_id=u.id
            WHERE n.user_id=%s ORDER BY n.created_at DESC LIMIT 50''',(uid,))
        run(c,'UPDATE notifications SET is_read=true WHERE user_id=%s',(uid,))
        c.commit()
        return render_template('notifications.html',notifs=rows or [],user=get_user(uid))
    finally: release_db(c)

# ── EXPLORE ──────────────────────────────────────────────────
@app.route('/explore')
def explore():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid=session['user_id']
    cat=request.args.get('category',''); sub=request.args.get('sub','')
    tag=request.args.get('tag',''); country=request.args.get('country','')
    city=request.args.get('city','')
    c=get_db()
    try:
        base='''SELECT DISTINCT u.*,
            (SELECT COUNT(*) FROM follows WHERE followed_id=u.id AND status='active') as follower_count,
            (SELECT COUNT(*)>0 FROM follows WHERE follower_id=%s AND followed_id=u.id AND status='active') as i_follow
            FROM users u'''
        if city: users=run(c,base+' JOIN user_tags t ON u.id=t.user_id WHERE t.city=%s LIMIT 50',(uid,city))
        elif country: users=run(c,base+' JOIN user_tags t ON u.id=t.user_id WHERE t.country=%s LIMIT 50',(uid,country))
        elif sub: users=run(c,base+' JOIN user_tags t ON u.id=t.user_id WHERE t.subcategory=%s LIMIT 50',(uid,sub))
        elif cat: users=run(c,base+' JOIN user_tags t ON u.id=t.user_id WHERE t.category=%s LIMIT 50',(uid,cat))
        else: users=run(c,base+' WHERE u.is_banned=false ORDER BY u.created_at DESC LIMIT 50',(uid,))

        pbase='''SELECT p.*,u.username,u.full_name,u.avatar_color,u.avatar,u.is_verified,
            (SELECT COUNT(*) FROM likes WHERE post_id=p.id) as like_count,
            (SELECT COUNT(*) FROM likes WHERE post_id=p.id AND user_id=%s) as liked,
            (SELECT COUNT(*) FROM comments WHERE post_id=p.id) as comment_count,0 as bookmarked
            FROM posts p JOIN users u ON p.user_id=u.id WHERE p.is_approved=true'''
        if tag:
            posts=run(c,pbase+''' AND p.id IN(SELECT post_id FROM post_hashtags ph
                JOIN hashtags h ON ph.hashtag_id=h.id WHERE h.tag=%s)
                ORDER BY p.created_at DESC LIMIT 30''',(uid,tag.lower()))
        else:
            posts=run(c,pbase+' ORDER BY like_count DESC LIMIT 20',(uid,))

        trending=run(c,'SELECT tag,post_count FROM hashtags ORDER BY post_count DESC LIMIT 15')
        return render_template('explore.html',
            users=users or [],posts=posts or [],trending=trending or [],
            selected_cat=cat,selected_sub=sub,selected_tag=tag,
            selected_country=country,selected_city=city,
            current_user=get_user(uid))
    finally: release_db(c)

# ── GROUPS ───────────────────────────────────────────────────
@app.route('/groups')
def groups():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid=session['user_id']; c=get_db()
    try:
        my=run(c,'SELECT g.*,gm.role FROM groups g JOIN group_members gm ON g.id=gm.group_id WHERE gm.user_id=%s ORDER BY g.created_at DESC',(uid,))
        all_g=run(c,'''SELECT g.*,
            (SELECT COUNT(*) FROM group_members WHERE group_id=g.id) as member_count,
            (SELECT COUNT(*)>0 FROM group_members WHERE group_id=g.id AND user_id=%s) as is_member
            FROM groups g ORDER BY member_count DESC LIMIT 20''',(uid,))
        return render_template('groups.html',my_groups=my or [],all_groups=all_g or [])
    finally: release_db(c)

@app.route('/groups/create', methods=['POST'])
def create_group():
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    d=request.get_json(silent=True) or {}; uid=session['user_id']; c=get_db()
    try:
        run(c,'INSERT INTO groups(name,description,category,is_private,owner_id) VALUES(%s,%s,%s,%s,%s)',
            (d.get('name'),d.get('description'),d.get('category'),d.get('is_private',False),uid))
        gid=run(c,'SELECT id FROM groups WHERE owner_id=%s ORDER BY created_at DESC LIMIT 1',(uid,),one=True)['id']
        run(c,'INSERT INTO group_members(group_id,user_id,role) VALUES(%s,%s,%s)',(gid,uid,'admin'))
        c.commit(); return jsonify({'success':True,'id':gid})
    finally: release_db(c)

@app.route('/groups/<int:gid>')
def group_detail(gid):
    if 'user_id' not in session: return redirect(url_for('login'))
    uid=session['user_id']; c=get_db()
    try:
        g=run(c,'SELECT * FROM groups WHERE id=%s',(gid,),one=True)
        if not g: return redirect(url_for('groups'))
        members=run(c,'SELECT u.*,gm.role FROM group_members gm JOIN users u ON gm.user_id=u.id WHERE gm.group_id=%s ORDER BY gm.role DESC',(gid,))
        posts=run(c,'SELECT gp.*,u.username,u.full_name,u.avatar_color,u.avatar FROM group_posts gp JOIN users u ON gp.user_id=u.id WHERE gp.group_id=%s ORDER BY gp.created_at DESC LIMIT 30',(gid,))
        ism=run(c,'SELECT 1 FROM group_members WHERE group_id=%s AND user_id=%s',(gid,uid),one=True)
        return render_template('group_detail.html',group=g,members=members or [],posts=posts or [],is_member=bool(ism))
    finally: release_db(c)

@app.route('/groups/<int:gid>/join', methods=['POST'])
def join_group(gid):
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    uid=session['user_id']; c=get_db()
    try:
        ex=run(c,'SELECT 1 FROM group_members WHERE group_id=%s AND user_id=%s',(gid,uid),one=True)
        if ex: run(c,'DELETE FROM group_members WHERE group_id=%s AND user_id=%s',(gid,uid)); joined=False
        else: run(c,'INSERT INTO group_members(group_id,user_id) VALUES(%s,%s)',(gid,uid)); joined=True
        c.commit(); return jsonify({'joined':joined})
    finally: release_db(c)

@app.route('/groups/<int:gid>/post', methods=['POST'])
def group_post(gid):
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    uid=session['user_id']; txt=(request.get_json(silent=True) or {}).get('content','').strip()
    if not txt: return jsonify({'error':'Boş olamaz'}),400
    c=get_db()
    try:
        run(c,'INSERT INTO group_posts(group_id,user_id,content) VALUES(%s,%s,%s)',(gid,uid,txt))
        c.commit(); return jsonify({'success':True})
    finally: release_db(c)

# ── EVENTS ───────────────────────────────────────────────────
@app.route('/events')
def events():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid=session['user_id']; c=get_db()
    try:
        rows=run(c,'''SELECT e.*,u.username,u.full_name,
            (SELECT COUNT(*) FROM event_attendees WHERE event_id=e.id AND status='going') as going_count,
            (SELECT status FROM event_attendees WHERE event_id=e.id AND user_id=%s) as my_status
            FROM events e JOIN users u ON e.user_id=u.id
            WHERE e.event_date>=CURRENT_DATE ORDER BY e.event_date ASC LIMIT 20''',(uid,))
        return render_template('events.html',events=rows or [])
    finally: release_db(c)

@app.route('/events/create', methods=['POST'])
def create_event():
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    d=request.get_json(silent=True) or {}; uid=session['user_id']
    title=(d.get('title') or '').strip()
    if not title: return jsonify({'error':'Etkinlik adı zorunlu'}),400
    edate=d.get('event_date','')
    if not edate: return jsonify({'error':'Tarih zorunlu'}),400
    c=get_db()
    try:
        run(c,'INSERT INTO events(user_id,title,description,location,event_date,event_time) VALUES(%s,%s,%s,%s,%s,%s)',
            (uid,title,d.get('description'),d.get('location'),edate,d.get('event_time')))
        c.commit(); return jsonify({'success':True})
    except Exception as e:
        c.rollback(); return jsonify({'error':f'Etkinlik oluşturma hatası: {str(e)[:100]}'}),500
    finally: release_db(c)

@app.route('/events/<int:eid>/attend', methods=['POST'])
def attend_event(eid):
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    uid=session['user_id']; st=(request.get_json(silent=True) or {}).get('status','going')
    c=get_db()
    try:
        run(c,'INSERT INTO event_attendees VALUES(%s,%s,%s) ON CONFLICT(event_id,user_id) DO UPDATE SET status=%s',
            (eid,uid,st,st))
        c.commit(); return jsonify({'success':True})
    finally: release_db(c)

# ── JOBS ─────────────────────────────────────────────────────
@app.route('/jobs')
def jobs():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid=session['user_id']; cat=request.args.get('cat',''); c=get_db()
    try:
        sql='''SELECT j.*,u.username,u.full_name,u.avatar_color,
            (SELECT COUNT(*) FROM job_applications WHERE job_id=j.id) as app_count,
            (SELECT COUNT(*)>0 FROM saved_jobs WHERE job_id=j.id AND user_id=%s) as saved
            FROM jobs j JOIN users u ON j.user_id=u.id WHERE j.is_active=true'''
        rows=run(c,sql+(' AND j.job_type=%s ORDER BY j.created_at DESC LIMIT 30'if cat else ' ORDER BY j.created_at DESC LIMIT 30'),
            (uid,cat) if cat else (uid,))
        return render_template('jobs.html',jobs=rows or [],cat=cat)
    finally: release_db(c)

@app.route('/jobs/create', methods=['POST'])
def create_job():
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    d=request.get_json(silent=True) or {}; uid=session['user_id']; c=get_db()
    try:
        run(c,'INSERT INTO jobs(user_id,title,company,location,job_type,description,salary) VALUES(%s,%s,%s,%s,%s,%s,%s)',
            (uid,d.get('title'),d.get('company'),d.get('location'),d.get('job_type'),d.get('description'),d.get('salary')))
        c.commit(); return jsonify({'success':True})
    finally: release_db(c)

@app.route('/jobs/<int:jid>/apply', methods=['POST'])
def apply_job(jid):
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    uid=session['user_id']; d=request.get_json(silent=True) or {}; c=get_db()
    try:
        if run(c,'SELECT 1 FROM job_applications WHERE job_id=%s AND user_id=%s',(jid,uid),one=True):
            return jsonify({'error':'Zaten başvurdunuz'}),400
        run(c,'INSERT INTO job_applications(job_id,user_id,cover_letter) VALUES(%s,%s,%s)',(jid,uid,d.get('cover_letter','')))
        j=run(c,'SELECT user_id FROM jobs WHERE id=%s',(jid,),one=True)
        if j: notif(j['user_id'],uid,'job_apply',jid)
        c.commit(); return jsonify({'success':True})
    finally: release_db(c)

@app.route('/jobs/<int:jid>/save', methods=['POST'])
def save_job(jid):
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    uid=session['user_id']; c=get_db()
    try:
        ex=run(c,'SELECT 1 FROM saved_jobs WHERE user_id=%s AND job_id=%s',(uid,jid),one=True)
        if ex: run(c,'DELETE FROM saved_jobs WHERE user_id=%s AND job_id=%s',(uid,jid)); saved=False
        else: run(c,'INSERT INTO saved_jobs VALUES(%s,%s)',(uid,jid)); saved=True
        c.commit(); return jsonify({'saved':saved})
    finally: release_db(c)

# ── Q&A ──────────────────────────────────────────────────────
@app.route('/questions')
def questions():
    if 'user_id' not in session: return redirect(url_for('login'))
    c=get_db()
    try:
        rows=run(c,'''SELECT q.*,u.username,u.full_name,u.avatar_color,
            (SELECT COUNT(*) FROM answers WHERE question_id=q.id) as answer_count
            FROM questions q JOIN users u ON q.user_id=u.id ORDER BY q.created_at DESC LIMIT 30''')
        return render_template('questions.html',questions=rows or [])
    finally: release_db(c)

@app.route('/questions/ask', methods=['POST'])
def ask_question():
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    d=request.get_json(silent=True) or {}; uid=session['user_id']; c=get_db()
    try:
        run(c,'INSERT INTO questions(user_id,title,content,category,tags) VALUES(%s,%s,%s,%s,%s)',
            (uid,d.get('title'),d.get('content'),d.get('category'),d.get('tags','')))
        c.commit(); return jsonify({'success':True})
    finally: release_db(c)

@app.route('/questions/<int:qid>')
def question_detail(qid):
    if 'user_id' not in session: return redirect(url_for('login'))
    c=get_db()
    try:
        q=run(c,'SELECT q2.*,u.username,u.full_name,u.avatar_color FROM questions q2 JOIN users u ON q2.user_id=u.id WHERE q2.id=%s',(qid,),one=True)
        if not q: return redirect(url_for('questions'))
        run(c,'UPDATE questions SET views=views+1 WHERE id=%s',(qid,))
        ans=run(c,'SELECT a.*,u.username,u.full_name,u.avatar_color FROM answers a JOIN users u ON a.user_id=u.id WHERE a.question_id=%s ORDER BY a.is_accepted DESC,a.created_at ASC',(qid,))
        c.commit()
        return render_template('question_detail.html',question=q,answers=ans or [])
    finally: release_db(c)

@app.route('/questions/<int:qid>/answer', methods=['POST'])
def answer_question(qid):
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    d=request.get_json(silent=True) or {}; uid=session['user_id']; c=get_db()
    try:
        run(c,'INSERT INTO answers(question_id,user_id,content) VALUES(%s,%s,%s)',(qid,uid,d.get('content','')))
        q=run(c,'SELECT user_id FROM questions WHERE id=%s',(qid,),one=True)
        if q: notif(q['user_id'],uid,'answer',qid)
        c.commit(); return jsonify({'success':True})
    finally: release_db(c)

# ── SEARCH ───────────────────────────────────────────────────
@app.route('/search')
def search():
    if 'user_id' not in session: return redirect(url_for('login'))
    sq=request.args.get('q','').strip(); uid=session['user_id']; c=get_db()
    try:
        if sq:
            users=run(c,'''SELECT u.*,(SELECT COUNT(*) FROM follows WHERE followed_id=u.id AND status='active') as follower_count
                FROM users u WHERE (u.username ILIKE %s OR u.full_name ILIKE %s) AND u.is_banned=false LIMIT 20''',(f'%{sq}%',f'%{sq}%'))
            posts=run(c,'''SELECT p.*,u.username,u.full_name,u.avatar_color,u.avatar,u.is_verified,
                (SELECT COUNT(*) FROM likes WHERE post_id=p.id) as like_count,
                (SELECT COUNT(*) FROM likes WHERE post_id=p.id AND user_id=%s) as liked,
                (SELECT COUNT(*) FROM comments WHERE post_id=p.id) as comment_count,0 as bookmarked
                FROM posts p JOIN users u ON p.user_id=u.id WHERE p.content ILIKE %s ORDER BY p.created_at DESC LIMIT 20''',(uid,f'%{sq}%'))
            tags=run(c,'SELECT * FROM hashtags WHERE tag ILIKE %s LIMIT 10',(f'%{sq.lower().strip("#")}%',))
        else: users=posts=tags=[]
        return render_template('search.html',users=users or [],posts=posts or [],
            tags=tags or [],q=sq,current_user=get_user(uid))
    finally: release_db(c)

@app.route('/leaderboard')
def leaderboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    c=get_db()
    try:
        rows=run(c,'''SELECT u.*,
            (SELECT COUNT(*) FROM follows WHERE followed_id=u.id AND status='active') as follower_count,
            (SELECT COUNT(*) FROM posts WHERE user_id=u.id) as real_post_count
            FROM users u WHERE u.is_banned=false ORDER BY follower_count DESC,real_post_count DESC LIMIT 50''')
        return render_template('leaderboard.html',top_users=rows or [])
    finally: release_db(c)

@app.route('/bookmarks')
def bookmarks():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid=session['user_id']; c=get_db()
    try:
        rows=run(c,'''SELECT p.*,u.username,u.full_name,u.avatar_color,u.avatar,u.is_verified,
            (SELECT COUNT(*) FROM likes WHERE post_id=p.id) as like_count,
            (SELECT COUNT(*) FROM likes WHERE post_id=p.id AND user_id=%s) as liked,
            (SELECT COUNT(*) FROM comments WHERE post_id=p.id) as comment_count,1 as bookmarked
            FROM bookmarks b JOIN posts p ON b.post_id=p.id JOIN users u ON p.user_id=u.id
            WHERE b.user_id=%s ORDER BY b.created_at DESC''',(uid,uid))
        return render_template('bookmarks.html',posts=rows or [],user=get_user(uid))
    finally: release_db(c)

@app.route('/settings', methods=['GET','POST'])
def settings():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid=session['user_id']
    if request.method=='POST':
        d=request.get_json(silent=True) or {}; c=get_db()
        try:
            if d.get('action')=='change_password':
                u=run(c,'SELECT * FROM users WHERE id=%s',(uid,),one=True)
                if not u or not verify_pw(d.get('old_password',''), u.get('password_hash','')):
                    return jsonify({'error':'Mevcut şifre yanlış'}),400
                if len(d.get('new_password',''))<8: return jsonify({'error':'Yeni şifre en az 8 karakter'}),400
                run(c,'UPDATE users SET password_hash=%s WHERE id=%s',(hash_pw(d.get('new_password','')),uid))
                c.commit(); return jsonify({'success':True,'message':'Şifre değiştirildi'})
            run(c,'''INSERT INTO user_settings(user_id,email_notifs,push_notifs,show_online,theme)
                VALUES(%s,%s,%s,%s,%s)
                ON CONFLICT(user_id) DO UPDATE SET email_notifs=%s,push_notifs=%s,show_online=%s,theme=%s''',
              (uid,d.get('email_notifs',True),d.get('push_notifs',True),d.get('show_online',True),d.get('theme','dark'),
               d.get('email_notifs',True),d.get('push_notifs',True),d.get('show_online',True),d.get('theme','dark')))
            c.commit(); return jsonify({'success':True})
        finally: release_db(c)
    c=get_db()
    try:
        s=run(c,'SELECT * FROM user_settings WHERE user_id=%s',(uid,),one=True)
        return render_template('settings.html',user=get_user(uid),settings=s)
    finally: release_db(c)

@app.route('/followers/<username>')
def followers(username):
    if 'user_id' not in session: return redirect(url_for('login'))
    c=get_db()
    try:
        pu=run(c,'SELECT * FROM users WHERE username=%s',(username,),one=True)
        if not pu: return redirect(url_for('explore'))
        users=run(c,
            "SELECT u.*,(SELECT COUNT(*) FROM follows WHERE followed_id=u.id AND status='active') as follower_count"
            " FROM follows f JOIN users u ON f.follower_id=u.id"
            " WHERE f.followed_id=%s AND f.status='active'",(pu['id'],))
        return render_template('follow_list.html',users=users or [],profile_user=pu,
            list_type='Takipçiler',current_user=get_user(session['user_id']))
    finally: release_db(c)

@app.route('/following/<username>')
def following(username):
    if 'user_id' not in session: return redirect(url_for('login'))
    c=get_db()
    try:
        pu=run(c,'SELECT * FROM users WHERE username=%s',(username,),one=True)
        if not pu: return redirect(url_for('explore'))
        users=run(c,
            "SELECT u.*,(SELECT COUNT(*) FROM follows WHERE followed_id=u.id AND status='active') as follower_count"
            " FROM follows f JOIN users u ON f.followed_id=u.id"
            " WHERE f.follower_id=%s AND f.status='active'",(pu['id'],))
        return render_template('follow_list.html',users=users or [],profile_user=pu,
            list_type='Takip Edilenler',current_user=get_user(session['user_id']))
    finally: release_db(c)

# ── EMAIL DOĞRULAMA ──────────────────────────────────────────
@app.route('/verify_email/<token>')
def verify_email(token):
    c=get_db()
    try:
        row=run(c,'SELECT * FROM email_verifications WHERE token=%s',(token,),one=True)
        if not row: return render_template('verify_result.html',success=False,msg='Geçersiz veya süresi dolmuş link.')
        run(c,'UPDATE users SET email_verified=true WHERE id=%s',(row['user_id'],))
        run(c,'DELETE FROM email_verifications WHERE token=%s',(token,))
        c.commit()
        return render_template('verify_result.html',success=True,msg='E-posta başarıyla doğrulandı!')
    finally: release_db(c)

@app.route('/resend_verification', methods=['POST'])
def resend_verification():
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    uid=session['user_id']; user=get_user(uid)
    if user['email_verified']: return jsonify({'error':'E-posta zaten doğrulanmış'}),400
    c=get_db()
    try:
        tok=gen_token()
        run(c,'DELETE FROM email_verifications WHERE user_id=%s',(uid,))
        run(c,'INSERT INTO email_verifications(user_id,token) VALUES(%s,%s)',(uid,tok))
        c.commit()
        base_url = request.host_url.rstrip('/')
        url = f"{base_url}/verify_email/{tok}"
        # TODO: Gerçek e-posta gönderimi entegre edilecek (SendGrid/Mailgun)
        return jsonify({'success':True,'message':'Doğrulama bağlantısı oluşturuldu'})
    finally: release_db(c)

# ── SERTİFİKA ────────────────────────────────────────────────
@app.route('/upload_certificate', methods=['POST'])
def upload_certificate():
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    uid=session['user_id']
    name=(request.form.get('name') or '').strip()
    if not name: return jsonify({'error':'Sertifika adı zorunlu'}),400
    file_url=None
    if 'file' in request.files:
        f=request.files['file']
        if f and f.filename and ok_file(f.filename):
            ext=f.filename.rsplit('.',1)[-1].lower()
            if ext not in {'pdf','png','jpg','jpeg'}: return jsonify({'error':'Sadece PDF/görsel'}),400
            path,_=save_file(f,'certificates'); file_url=path
    c=get_db()
    try:
        yr=request.form.get('year','')
        run(c,'INSERT INTO certificates(user_id,name,issuer,year,file_url) VALUES(%s,%s,%s,%s,%s)',
            (uid,name,request.form.get('issuer',''),int(yr) if yr else None,file_url))
        cid=run(c,'SELECT id FROM certificates WHERE user_id=%s ORDER BY id DESC LIMIT 1',(uid,),one=True)['id']
        c.commit(); return jsonify({'success':True,'id':cid})
    finally: release_db(c)

@app.route('/delete_certificate/<int:cid>', methods=['POST'])
def delete_certificate(cid):
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    uid=session['user_id']; c=get_db()
    try:
        if not run(c,'SELECT 1 FROM certificates WHERE id=%s AND user_id=%s',(cid,uid),one=True):
            return jsonify({'error':'Bulunamadı'}),404
        run(c,'DELETE FROM certificates WHERE id=%s',(cid,))
        c.commit(); return jsonify({'success':True})
    finally: release_db(c)

# ── ADMIN ────────────────────────────────────────────────────
def is_admin(uid):
    u=get_user(uid); return bool(u and u.get('is_admin'))

@app.route('/admin')
def admin_panel():
    if 'user_id' not in session: return redirect(url_for('login'))
    if not is_admin(session['user_id']): return redirect(url_for('feed'))
    c=get_db()
    try:
        st={
            'total_users': run(c,'SELECT COUNT(*) as n FROM users',one=True)['n'],
            'verified_users': run(c,'SELECT COUNT(*) as n FROM users WHERE email_verified=true',one=True)['n'],
            'total_posts': run(c,'SELECT COUNT(*) as n FROM posts',one=True)['n'],
            'pending_posts': run(c,'SELECT COUNT(*) as n FROM posts WHERE is_approved=false',one=True)['n'],
            'open_reports': run(c,"SELECT COUNT(*) as n FROM reports WHERE status='pending'",one=True)['n'],
            'today_users': run(c,'SELECT COUNT(*) as n FROM users WHERE created_at::date=CURRENT_DATE',one=True)['n'],
            'bot_count': run(c,'SELECT COUNT(*) as n FROM users WHERE is_bot=true',one=True)['n'],
        }
        pp=run(c,'SELECT p.*,u.username,u.full_name,u.avatar_color FROM posts p JOIN users u ON p.user_id=u.id WHERE p.is_approved=false ORDER BY p.created_at DESC LIMIT 20')
        reps=run(c,"SELECT r.*,u.username as reporter_username FROM reports r JOIN users u ON r.reporter_id=u.id WHERE r.status='pending' ORDER BY r.created_at DESC LIMIT 30")
        rusers=run(c,'SELECT * FROM users ORDER BY created_at DESC LIMIT 20')
        return render_template('admin.html',stats=st,pending_posts=pp or [],reports=reps or [],recent_users=rusers or [])
    finally: release_db(c)

@app.route('/admin/approve_post/<int:pid>', methods=['POST'])
def admin_approve_post(pid):
    if 'user_id' not in session or not is_admin(session['user_id']): return jsonify({'error':'Yetkisiz'}),403
    act=(request.get_json(silent=True) or {}).get('action','approve'); c=get_db()
    try:
        if act=='approve': run(c,'UPDATE posts SET is_approved=true WHERE id=%s',(pid,))
        else: run(c,'DELETE FROM posts WHERE id=%s',(pid,))
        c.commit(); return jsonify({'success':True})
    finally: release_db(c)

@app.route('/admin/resolve_report/<int:rid>', methods=['POST'])
def admin_resolve_report(rid):
    if 'user_id' not in session or not is_admin(session['user_id']): return jsonify({'error':'Yetkisiz'}),403
    act=(request.get_json(silent=True) or {}).get('action','dismiss'); c=get_db()
    try:
        run(c,'UPDATE reports SET status=%s WHERE id=%s',(act,rid))
        if act=='ban':
            r=run(c,'SELECT * FROM reports WHERE id=%s',(rid,),one=True)
            if r and r['target_type']=='user': run(c,'UPDATE users SET is_banned=true WHERE id=%s',(r['target_id'],))
        c.commit(); return jsonify({'success':True})
    finally: release_db(c)

@app.route('/admin/toggle_verify/<int:uid>', methods=['POST'])
def admin_toggle_verify(uid):
    if 'user_id' not in session or not is_admin(session['user_id']): return jsonify({'error':'Yetkisiz'}),403
    c=get_db()
    try:
        u=run(c,'SELECT is_verified FROM users WHERE id=%s',(uid,),one=True)
        nv=not u['is_verified']
        run(c,'UPDATE users SET is_verified=%s WHERE id=%s',(nv,uid))
        c.commit(); return jsonify({'verified':nv})
    finally: release_db(c)

@app.route('/admin/ban_user/<int:uid>', methods=['POST'])
def admin_ban_user(uid):
    if 'user_id' not in session or not is_admin(session['user_id']): return jsonify({'error':'Yetkisiz'}),403
    c=get_db()
    try:
        u=run(c,'SELECT is_banned FROM users WHERE id=%s',(uid,),one=True)
        nv=not u['is_banned']
        run(c,'UPDATE users SET is_banned=%s WHERE id=%s',(nv,uid))
        c.commit(); return jsonify({'banned':nv})
    finally: release_db(c)

@app.route('/admin/make_admin/<int:uid>', methods=['POST'])
def admin_make_admin(uid):
    if 'user_id' not in session or not is_admin(session['user_id']): return jsonify({'error':'Yetkisiz'}),403
    c=get_db()
    try:
        u=run(c,'SELECT is_admin FROM users WHERE id=%s',(uid,),one=True)
        nv=not u['is_admin']
        run(c,'UPDATE users SET is_admin=%s WHERE id=%s',(nv,uid))
        c.commit(); return jsonify({'is_admin':nv})
    finally: release_db(c)

@app.route('/admin/delete_post/<int:pid>', methods=['POST'])
def admin_delete_post(pid):
    if 'user_id' not in session or not is_admin(session['user_id']): return jsonify({'error':'Yetkisiz'}),403
    c=get_db()
    try:
        run(c,'DELETE FROM posts WHERE id=%s',(pid,))
        c.commit(); return jsonify({'success':True})
    finally: release_db(c)

# ── API ──────────────────────────────────────────────────────
@app.route('/api/cities/<country>')
def get_cities(country): return jsonify({'cities':COUNTRY_CITIES.get(country,[])})

@app.route('/api/profile_score')
def profile_score():
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    uid=session['user_id']; u=get_user(uid); c=get_db()
    try:
        tags=run(c,'SELECT COUNT(*) as n FROM user_tags WHERE user_id=%s',(uid,),one=True)['n']
        edu=run(c,'SELECT COUNT(*) as n FROM education WHERE user_id=%s',(uid,),one=True)['n']
        exp=run(c,'SELECT COUNT(*) as n FROM experience WHERE user_id=%s',(uid,),one=True)['n']
    finally: release_db(c)
    items=[(bool(u.get('full_name')),15,'Ad Soyad'),(bool(u.get('bio')),15,'Biyografi'),
           (bool(u.get('avatar')),15,'Profil Fotoğrafı'),(bool(u.get('cover_photo')),10,'Kapak Fotoğrafı'),
           (bool(u.get('age')),10,'Yaş'),(bool(u.get('location')),5,'Konum'),
           (tags>=1,15,'Meslek/Alan'),(edu>=1,5,'Eğitim'),(exp>=1,5,'Deneyim')]
    score=sum(p for ok,p,_ in items if ok)
    return jsonify({'score':score,'checks':[{'label':l,'done':ok,'points':p} for ok,p,l in items],
        'tips':[l for ok,p,l in items if not ok][:3]})

@app.route('/api/ai/suggest_posts')
def suggest_posts():
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    c=get_db()
    try: tags=run(c,'SELECT subcategory FROM user_tags WHERE user_id=%s LIMIT 5',(session['user_id'],))
    finally: release_db(c)
    base=["Bugün alanımda öğrendiğim: ...","Kariyerimde aldığım en iyi karar: ...","Yeni başlayanlara tavsiyem: ...","Bu hafta çalıştığım konu: ...","Sektördeki son gelişmeler: ..."]
    ex=[f"{t['subcategory']} alanında bugün: ..." for t in (tags or [])]
    all_p=base+ex; random.shuffle(all_p)
    return jsonify({'suggestions':all_p[:6]})

@app.route('/api/hashtag_suggest', methods=['POST'])
@app.route('/api/ai/hashtag_suggest', methods=['POST'])
def hashtag_suggest():
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    content=(request.get_json(silent=True) or {}).get('content','').lower()
    kw={'python':['python','yazilim'],'javascript':['javascript','frontend'],'yapay':['yapayZeka','ai'],
        'kariyer':['kariyer','networking'],'eğitim':['egitim'],'teknoloji':['teknoloji','tech'],
        'sağlık':['saglik'],'tasarım':['tasarim','ui'],'spor':['spor','fitness'],'finans':['finans']}
    sug=set()
    for k,v in kw.items():
        if k in content: sug.update(v)
    if not sug: sug={'sterk','paylasim'}
    return jsonify({'hashtags':list(sug)[:6]})

# ── MISSING API ENDPOINTS ────────────────────────────────────
@app.route('/api/daily_progress')
def daily_progress():
    if 'user_id' not in session: return jsonify({'tasks':[],'total_xp':0})
    uid=session['user_id']; c=get_db()
    try:
        has_post=run(c,"SELECT COUNT(*)>0 as ok FROM posts WHERE user_id=%s AND created_at::date=CURRENT_DATE",(uid,),one=True)
        has_like=run(c,"SELECT COUNT(*)>0 as ok FROM likes WHERE user_id=%s AND post_id IN(SELECT id FROM posts WHERE created_at::date=CURRENT_DATE)",(uid,),one=True)
        has_comment=run(c,"SELECT COUNT(*)>0 as ok FROM comments WHERE user_id=%s AND created_at::date=CURRENT_DATE",(uid,),one=True)
        has_follow=run(c,"SELECT COUNT(*)>0 as ok FROM follows WHERE follower_id=%s AND created_at::date=CURRENT_DATE",(uid,),one=True)
        tasks=[
            {'label':'Bir gönderi paylaş','done':bool(has_post and has_post['ok']),'xp':10},
            {'label':'Bir gönderi beğen','done':bool(has_like and has_like['ok']),'xp':5},
            {'label':'Yorum yap','done':bool(has_comment and has_comment['ok']),'xp':5},
            {'label':'Birini takip et','done':bool(has_follow and has_follow['ok']),'xp':10},
        ]
        total_xp=sum(t['xp'] for t in tasks if t['done'])
        return jsonify({'tasks':tasks,'total_xp':total_xp})
    except: return jsonify({'tasks':[],'total_xp':0})
    finally: release_db(c)

@app.route('/api/ai/suggest_users')
def ai_suggest_users():
    if 'user_id' not in session: return jsonify({'users':[]})
    uid=session['user_id']; c=get_db()
    try:
        users=run(c,'''SELECT DISTINCT u.id,u.username,u.full_name,u.avatar_color,u.avatar,
            (SELECT COUNT(*) FROM follows WHERE followed_id=u.id AND status='active') as follower_count,
            (SELECT COUNT(*) FROM user_tags WHERE user_id=u.id AND subcategory IN
                (SELECT subcategory FROM user_tags WHERE user_id=%s)) as common_tags
            FROM users u
            WHERE u.id!=%s AND u.is_banned=false
            AND u.id NOT IN(SELECT followed_id FROM follows WHERE follower_id=%s AND status='active')
            AND u.id NOT IN(SELECT blocked_id FROM blocks WHERE blocker_id=%s)
            ORDER BY common_tags DESC, follower_count DESC LIMIT 8''',(uid,uid,uid,uid))
        return jsonify({'users':[dict(u) for u in (users or [])]})
    except: return jsonify({'users':[]})
    finally: release_db(c)

# ── GERİ BİLDİRİM (İstek/Öneri/Şikayet) ────────────────────
@app.route('/feedback')
def feedback_page():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid=session['user_id']; c=get_db()
    try:
        my=run(c,'SELECT * FROM feedback WHERE user_id=%s ORDER BY created_at DESC LIMIT 20',(uid,))
        return render_template('feedback.html',feedbacks=my or [],user=get_user(uid))
    finally: release_db(c)

@app.route('/feedback/submit', methods=['POST'])
def submit_feedback():
    if 'user_id' not in session: return jsonify({'error':'Giriş gerekli'}),401
    d=request.get_json(silent=True) or {}; uid=session['user_id']
    ftype=(d.get('type') or '').strip()
    subject=(d.get('subject') or '').strip()
    content=(d.get('content') or '').strip()
    if ftype not in ('istek','oneri','sikayet'): return jsonify({'error':'Geçersiz geri bildirim türü'}),400
    if not content: return jsonify({'error':'İçerik zorunlu'}),400
    c=get_db()
    try:
        run(c,'INSERT INTO feedback(user_id,feedback_type,subject,content) VALUES(%s,%s,%s,%s)',
            (uid,ftype,subject,content))
        c.commit(); return jsonify({'success':True})
    except Exception as e:
        c.rollback(); return jsonify({'error':str(e)}),500
    finally: release_db(c)

@app.route('/admin/feedbacks')
def admin_feedbacks():
    if 'user_id' not in session or not is_admin(session['user_id']): return redirect(url_for('feed'))
    c=get_db()
    try:
        rows=run(c,'SELECT f.*,u.username,u.full_name FROM feedback f JOIN users u ON f.user_id=u.id ORDER BY f.created_at DESC LIMIT 50')
        return jsonify([dict(r) for r in (rows or [])])
    finally: release_db(c)

@app.route('/admin/feedback/<int:fid>/reply', methods=['POST'])
def admin_reply_feedback(fid):
    if 'user_id' not in session or not is_admin(session['user_id']): return jsonify({'error':'Yetkisiz'}),403
    d=request.get_json(silent=True) or {}; c=get_db()
    try:
        run(c,'UPDATE feedback SET admin_reply=%s,status=%s WHERE id=%s',(d.get('reply',''),'replied',fid))
        c.commit(); return jsonify({'success':True})
    finally: release_db(c)

# ── HATA SAYFALARI ───────────────────────────────────────────
@app.errorhandler(404)
def e404(e): return render_template('404.html'),404
@app.errorhandler(500)
def e500(e): return render_template('404.html'),500

# ── BOT SİSTEMİ ──────────────────────────────────────────────
BOT_DATA = [
    {"username":"ayse_techbot","full_name":"Ayşe Teknoloji","cats":["Teknoloji & Yazılım"],"sub":"Backend Developer","posts":["Python ile bugün harika bir API yazdım 🐍 #python #backend","Mikro servis mimarisi hakkında yeni bir şeyler öğrendim! #teknoloji","Code review süreci ekibi nasıl güçlü kılar? #yazilim","Docker ve Kubernetes ile deployment çok daha kolay 🚀","TypeScript'e geçiş süreci düşüncelerim... #javascript"]},
    {"username":"mehmet_doctor","full_name":"Dr. Mehmet Yılmaz","cats":["Sağlık & Tıp"],"sub":"Pratisyen Hekim","posts":["Hastaların sağlığı her şeyin önünde gelir 🏥 #saglik","Preventif tıbbın önemi günümüzde daha da artıyor","Sağlıklı yaşam için en önemli 5 alışkanlık: #wellness","Tıp teknolojisindeki gelişmeler bizi heyecanlandırıyor 💊","Ekip çalışmasının hasta bakımına katkısı üzerine düşüncelerim"]},
    {"username":"zeynep_egitim","full_name":"Zeynep Öğretmen","cats":["Eğitim & Öğretmenler"],"sub":"Matematik Öğretmeni","posts":["Öğrencilerimin başarısı beni her gün motive ediyor 🎓 #egitim","Matematik öğretmenin en zevkli yanı: 'Anladım!' anı ✨","Gamification ile matematik derslerini eğlenceli hale getiriyorum","Dijital araçlarla eğitim: artıları ve eksileri #ogrenme","Her öğrenci farklı öğrenir, buna uyum sağlamak şart!"]},
    {"username":"ali_muhendis","full_name":"Ali Mühendis","cats":["Mühendislik"],"sub":"İnşaat Mühendisi","posts":["Sürdürülebilir yapı malzemeleri geleceğimiz 🏗️ #muhendislik","Yeni proje üzerinde çalışıyoruz, detayları yakında paylaşacağım!","Deprem dayanıklı yapı tasarımı üzerine araştırmalarım","BIM teknolojisi inşaat sektörünü nasıl dönüştürüyor? #insaat","Mühendislik etiği: Her kararın bir sorumluluğu var"]},
    {"username":"selin_hukuk","full_name":"Av. Selin Kaya","cats":["Hukuk"],"sub":"İş Hukuku Avukatı","posts":["Hukuki hakların farkında olmak her vatandaşın görevi ⚖️ #hukuk","İş hukuku davalarında dikkat edilmesi gerekenler","Yeni düzenlemeler iş dünyasını nasıl etkiliyor? #isHukuku","Uzlaşma ve arabuluculuk: Mahkeme yerine alternatif çözümler","Dijital sözleşmelerin hukuki geçerliliği üzerine"]},
    {"username":"can_sporcu","full_name":"Can Fitness","cats":["Spor & Fitness"],"sub":"Fitness Koçu","posts":["Sabah antrenmanı güne harika başlamak için en iyi yol! 💪 #fitness","Doğru beslenme olmadan antrenman yarım kalır #saglik","Motivasyonun düşük olduğu günlerde ne yapmalı?","Kardio mu, kuvvet mi? İkisinin de önemi var #spor","Uyku kalitesi performansı doğrudan etkiliyor 😴"]},
    {"username":"defne_tasarim","full_name":"Defne Tasarımcı","cats":["Sanat & Tasarım"],"sub":"Grafik Tasarımcı","posts":["Renk teorisi tasarımın temel taşı 🎨 #tasarim","Müşteri geri bildirimleri nasıl verimli kullanılır?","Minimalizm: Daha azıyla daha fazlasını anlatmak #ui","Font seçimi marka kimliğini nasıl şekillendirir?","Portfolyo hazırlamanın püf noktaları #grafik"]},
    {"username":"emre_finans","full_name":"Emre Finans","cats":["İş & Finans"],"sub":"Yatırım Uzmanı","posts":["Uzun vadeli yatırım stratejileri 📈 #finans","Enflasyona karşı portföy çeşitlendirmesi neden önemli?","Startup ekosistemi ve melek yatırımcılık üzerine","Finansal okuryazarlık herkesin bilmesi gereken temel beceri #yatirim","Kripto para: fırsat mı, risk mi? #blockchain"]},
]

def create_bots():
    """Bot kullanıcıları oluştur (sadece yoksa)."""
    try:
        c=get_db()
        try:
            for b in BOT_DATA:
                ex=run(c,'SELECT id FROM users WHERE username=%s',(b['username'],),one=True)
                if ex: continue
                color=random.choice(['#7c3aed','#a855f7','#10b981','#3b82f6','#f59e0b'])
                run(c,'''INSERT INTO users(username,email,password_hash,full_name,age,gender,
                         avatar_color,is_bot,email_verified,bio)
                         VALUES(%s,%s,%s,%s,%s,%s,%s,true,true,%s)''',
                    (b['username'],f"{b['username']}@sterk.bot",hash_pw(gen_token()),
                     b['full_name'],random.randint(24,45),random.choice(['Erkek','Kadın']),
                     color,f"{b['full_name']} — {b['sub']} uzmanı. Sterk'te profesyonellerle bağlantı kuruyorum."))
                uid=run(c,'SELECT id FROM users WHERE username=%s',(b['username'],),one=True)['id']
                run(c,'UPDATE users SET email_verified=true WHERE id=%s',(uid,))
                # Kategori
                run(c,'INSERT INTO user_tags(user_id,category,subcategory) VALUES(%s,%s,%s)',
                    (uid,b['cats'][0],b['sub']))
                run(c,'INSERT INTO category_changes(user_id,new_category) VALUES(%s,%s)',(uid,b['cats'][0]))
                # Settings
                run(c,'INSERT INTO user_settings(user_id) VALUES(%s) ON CONFLICT DO NOTHING',(uid,))
                c.commit()
                print(f"  🤖 Bot oluşturuldu: @{b['username']}")
        finally: release_db(c)
    except Exception as e:
        print(f"  ⚠️ Bot oluşturma hatası: {e}")

def bot_post_loop():
    """Arka planda çalışan bot paylaşım döngüsü."""
    import time as t
    t.sleep(15)  # Başlangıçta bekle
    while True:
        try:
            c=get_db()
            try:
                bots=run(c,'SELECT id,username FROM users WHERE is_bot=true AND is_banned=false')
                if bots:
                    bot=random.choice(bots)
                    # Bu bot için önceden tanımlanmış post listesinden seç
                    bdata=next((b for b in BOT_DATA if b['username']==bot['username']),None)
                    if bdata:
                        content=random.choice(bdata['posts'])
                        # %30 ihtimalle Genel, %70 kendi kategorisi
                        tcat='Genel' if random.random()<0.3 else bdata['cats'][0]
                        run(c,'''INSERT INTO posts(user_id,content,target_category,is_approved)
                                 VALUES(%s,%s,%s,true)''',(bot['id'],content,tcat))
                        pid=run(c,'SELECT id FROM posts WHERE user_id=%s ORDER BY created_at DESC LIMIT 1',(bot['id'],),one=True)['id']
                        _cur=c.cursor(); _cur.execute('SAVEPOINT bp'); _cur.close()
                        save_tags(pid,content,c)
                        run(c,'UPDATE users SET post_count=post_count+1 WHERE id=%s',(bot['id'],))
                        c.commit()

                # Botlar birbirini takip etsin (zaman zaman)
                if random.random()<0.1 and bots and len(bots)>1:
                    b1,b2=random.sample(list(bots),2)
                    ex=run(c,'SELECT 1 FROM follows WHERE follower_id=%s AND followed_id=%s',(b1['id'],b2['id']),one=True)
                    if not ex:
                        run(c,"INSERT INTO follows(follower_id,followed_id,status) VALUES(%s,%s,'active')",(b1['id'],b2['id']))
                        c.commit()

                # Botlar real kullanıcıların gönderilerini beğensin
                if random.random()<0.2 and bots:
                    bot=random.choice(bots)
                    posts=run(c,'SELECT id,user_id FROM posts WHERE user_id NOT IN(SELECT id FROM users WHERE is_bot=true) AND is_approved=true ORDER BY RANDOM() LIMIT 1',one=True)
                    if posts:
                        ex=run(c,'SELECT 1 FROM likes WHERE user_id=%s AND post_id=%s',(bot['id'],posts['id']),one=True)
                        if not ex:
                            run(c,'INSERT INTO likes(user_id,post_id) VALUES(%s,%s)',(bot['id'],posts['id']))
                            c.commit()
            finally: release_db(c)
        except Exception as e:
            pass  # Sessizce geç
        # 3-8 dakika arasında rastgele bekle
        t.sleep(random.randint(180,480))

# ── BAŞLAT ───────────────────────────────────────────────────
import sys, io
# Windows console Unicode fix
if sys.platform == 'win32':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception: pass

# Upload klasörlerini oluştur (hem local hem production)
for _d in ['avatars','posts','covers','certificates','shorts','stories']:
    os.makedirs(os.path.join(UPLOAD_FOLDER,_d),exist_ok=True)

# Production (gunicorn) ve local için DB başlat
def _startup():
    if not HAVE_PG:
        print('[HATA] psycopg2 eksik! -> pip install psycopg2-binary')
        return
    try:
        init_db()
        create_bots()
        print('[OK] DB ve botlar hazir')
        # Bot döngüsünü arka planda başlat
        bt=threading.Thread(target=bot_post_loop,daemon=True)
        bt.start()
        print('[OK] Bot sistemi aktif')
    except Exception as e:
        print(f'[HATA] DB baglanti hatasi: {e}')

_startup()

if __name__=='__main__':
    print('\n' + '='*58)
    print('  STERK v2 - Profesyonel Sosyal Ag')
    print(f'  Kategori kilidi: {CAT_LOCK_DAYS} gun')
    print('='*58)
    print('  http://localhost:5000\n')
    app.run(debug=True,port=5000,use_reloader=False)
