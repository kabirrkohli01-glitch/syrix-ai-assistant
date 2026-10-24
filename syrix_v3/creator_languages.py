"""The languages a creator question is recognised in, and the fixed reply in each.

Data only; the matching lives in creator.py. Kept apart so adding a language
is an edit to a table rather than to the logic that reads it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class Lang:
    """How one language asks who made you.

    A question matches when it holds a question word, a making verb and a
    second-person pronoun in any order, or "your" next to a maker noun, or
    one word that already means "made you" / "your maker" (Arabic, Turkish,
    Indonesian and Swahili fold the pronoun into the word).
    """
    code: str
    who: Tuple[str, ...]
    you: Tuple[str, ...]
    made: Tuple[str, ...]
    maker: Tuple[str, ...] = ()
    your: Tuple[str, ...] = ()
    joined: Tuple[str, ...] = ()
    # Scripts written without spaces between words are matched as substrings.
    unspaced: bool = False
    # Used instead of the word bags when set: see the Chinese entry.
    patterns: Tuple[str, ...] = ()


def _t(words: str) -> Tuple[str, ...]:
    return tuple(words.split())


LANGS: Tuple[Lang, ...] = (
    Lang("es", _t("quién quien quiénes quienes"), _t("te ti tú usted vos"),
          _t("creó creo hizo hicieron crearon construyó construyo construyeron desarrolló "
             "desarrollo desarrollaron programó programo diseñó diseño entrenó entreno "
             "inventó invento fabricó creado creada hecho desarrollado construido programado"),
          _t("creador creadora creadores desarrollador desarrolladores programador dueño "
             "dueña propietario autor inventor fabricante padre papá jefe"),
          _t("tu tus su")),
    Lang("pt", _t("quem"), _t("te você voce vc ti tu"),
          _t("criou fez construiu desenvolveu programou treinou inventou projetou criaram "
             "fizeram desenvolveram criado feito desenvolvido construído construido"),
          _t("criador criadora criadores desenvolvedor desenvolvedora programador dono dona "
             "proprietário autor inventor pai chefe"),
          _t("teu tua seu sua")),
    Lang("fr", _t("qui"), _t("t te toi vous tu"),
          _t("créé cree crée créée creee créés fait faite construit développé developpe "
             "développée conçu concu programmé programme entraîné entraine inventé invente "
             "fabriqué fabrique"),
          _t("créateur createur créatrice créateurs développeur developpeur développeurs "
             "concepteur propriétaire proprietaire auteur inventeur programmeur père pere "
             "patron fabricant"),
          _t("ton ta tes votre vos")),
    Lang("de", _t("wer wem wessen"), _t("dich dir du euch"),
          _t("gemacht erschaffen geschaffen erstellt entwickelt gebaut programmiert trainiert "
             "erfunden kreiert konstruiert entworfen hergestellt machte schuf entwickelte "
             "baute erstellte programmierte"),
          _t("schöpfer schoepfer ersteller entwickler erfinder besitzer eigentümer eigentuemer "
             "programmierer macher hersteller erschaffer vater chef"),
          _t("dein deine deinen deinem deiner deines")),
    Lang("it", _t("chi"), _t("ti te tu vi voi"),
          _t("creato creata fatto fatta costruito sviluppato programmato addestrato allenato "
             "inventato progettato ideato creò fece costruì sviluppò"),
          _t("creatore creatrice creatori sviluppatore sviluppatori proprietario padrone "
             "programmatore autore inventore papà capo"),
          _t("tuo tua tuoi tue vostro")),
    Lang("nl", _t("wie"), _t("je jou jij u"),
          _t("gemaakt gecreëerd gecreeerd gebouwd ontwikkeld geprogrammeerd getraind bedacht "
             "ontworpen uitgevonden maakte bouwde ontwikkelde"),
          _t("maker makers schepper ontwikkelaar ontwikkelaars eigenaar programmeur bedenker "
             "uitvinder baas vader"),
          _t("jouw je uw")),
    Lang("sv", _t("vem vilka"), _t("dig du er"),
          _t("skapade gjorde byggde utvecklade programmerade tränade skapat gjort byggt "
             "utvecklat uppfann"),
          _t("skapare utvecklare ägare agare tillverkare uppfinnare pappa chef"),
          _t("din ditt dina")),
    Lang("no", _t("hvem"), _t("deg du dere"),
          _t("laget lagde skapte skapt bygget bygde utviklet programmerte trente oppfant"),
          _t("skaper skaperen utvikler utvikleren eier eieren produsent oppfinner sjef"),
          _t("din ditt dine")),
    Lang("da", _t("hvem"), _t("dig du jer"),
          _t("lavede lavet skabte skabt byggede bygget udviklede udviklet programmerede "
             "trænede opfandt"),
          _t("skaber skaberen udvikler udvikleren ejer ejeren producent opfinder chef"),
          _t("din dit dine")),
    Lang("pl", _t("kto"), _t("cię cie ciebie tobą toba was ty"),
          _t("stworzył stworzyl stworzyła stworzyli zrobił zrobil zrobili zbudował zbudowal "
             "zaprojektował zaprogramował wytrenował opracował stworzony zrobiony"),
          _t("twórca tworca twórcą twórcy właściciel wlasciciel programista autor wynalazca "
             "szef ojciec"),
          _t("twój twoj twoja twoim twojego")),
    Lang("cs", _t("kdo"), _t("tě te tebe vás"),
          _t("vytvořil vytvoril vytvořili udělal udelal stvořil naprogramoval vyvinul "
             "vyvinuli postavil"),
          _t("tvůrce tvurce vývojář vyvojar majitel autor programátor"),
          _t("tvůj tvuj tvoje tvého")),
    Lang("ro", _t("cine"), _t("te tine"),
          _t("creat făcut facut construit dezvoltat programat antrenat inventat proiectat"),
          _t("creator creatorul creatorii dezvoltator dezvoltatorul proprietar proprietarul "
             "autor autorul programator"),
          _t("tău tau ta tale")),
    Lang("hu", _t("ki kik"), _t("téged teged titeket önt"),
          _t("csinált csinalt készített keszitett alkotott fejlesztett épített epitett "
             "teremtett programozott"),
          joined=_t("készítőd keszitod alkotód alkotod fejlesztőd fejlesztod teremtőd "
                    "tulajdonosod")),
    Lang("tr", _t("kim kimler"), _t("seni sizi sen siz"),
          _t("yaptı* yapti* yapıl* yapil* yarat* geliştir* gelistir* oluştur* olustur* "
             "programla* tasarla* kodla* eğitti* egitti* üretti* uretti*"),
          _t("yaratıcı* yaratici* geliştirici* gelistirici* sahib* yapımcı* yapimci* "
             "programcı* programci*"),
          _t("senin sizin"),
          _t("yaratıcın yaraticin geliştiricin gelistiricin sahibin yapımcın yapimcin "
             "programcın programcin")),
    Lang("ru", _t("кто"), _t("тебя вас тебе ты вы"),
          _t("созда* сдела* разработ* построил* придума* написал* обучил* запрограммирова* "
             "спроектирова* натренирова*"),
          _t("создател* разработчик* владел* автор* хозя* творец творц* программист* отец"),
          _t("твой твоя твоего твоим ваш ваша вашего")),
    Lang("uk", _t("хто"), _t("тебе вас ти ви"),
          _t("створи* зроби* розроби* розробл* побудува* придума* написа* навчи* "
             "запрограмува*"),
          _t("творець творц* розробник* власник* автор* хазя* батько"),
          _t("твій твоя твого ваш")),
    Lang("el", _t("ποιος ποιός ποια ποιοι"), _t("σε εσένα εσενα εσύ εσυ"),
          _t("έφτιαξε εφτιαξε έφτιαξαν δημιούργησε δημιουργησε δημιούργησαν ανέπτυξε "
             "ανεπτυξε ανέπτυξαν κατασκεύασε κατασκευασε προγραμμάτισε"),
          _t("δημιουργός δημιουργος κατασκευαστής κατασκευαστης προγραμματιστής "
             "ιδιοκτήτης ιδιοκτητης"),
          _t("σου")),
    Lang("he", _t("מי"), _t("אותך אתה"),
          _t("יצר יצרה יצרו בנה בנו פיתח פיתחו תכנת תכנתו עשה עשו אימן"),
          _t("היוצר יוצר המפתח מפתח הבעלים בעלים המתכנת"),
          _t("שלך")),
    Lang("ar", _t("من مين منو"), _t("انت أنت إنت انتي"),
          _t("صنع خلق صمم طور برمج أنشأ انشأ بنى درب ابتكر اخترع سوا"),
          joined=_t("صنعك خلقك صممك طورك برمجك أنشأك انشأك بناك دربك ابتكرك اخترعك سواك "
                    "صانعك مطورك مبرمجك خالقك مالكك منشئك مصممك مبتكرك مطوروك")),
    Lang("fa", _t("کی کسی"), _t("تو تورو تورا شما"),
          _t("ساخت ساخته ایجاد طراحی توسعه"),
          _t("سازنده خالق مالک"),
          joined=_t("ساختت سازندت سازنده‌ات خالقت مالکت")),
    Lang("ur", _t("کس کون کسنے"), _t("تمہیں تمھیں آپ تم تجھے آپکو"),
          _t("بنایا بنائی تخلیق تیار"),
          _t("خالق مالک بنانے"),
          _t("تمہارا تمہارے تمھارا آپکا تیرا تیرے")),
    Lang("hi", _t("किसने किस कौन किसका"), _t("तुम्हें तुम्हे तुमको तुम आपको आप तुझे तू तुझको"),
          _t("बना* निर्माण विकसित तैयार डिज़ाइन डिजाइन प्रोग्राम क्रिएट"),
          _t("निर्माता मालिक रचयिता बनाने* डेवलपर सृष्टिकर्ता जनक"),
          _t("तुम्हारा तुम्हारे तुम्हारी आपका आपके आपकी तेरा तेरे तेरी")),
    Lang("mr", _t("कोणी कुणी कोण"), _t("तुला तुम्हाला तू तुम्ही"),
          _t("बनव* तयार निर्माण विकसित"),
          _t("निर्माता मालक बनवणारा"),
          _t("तुझा तुझे तुझी तुमचा तुमचे")),
    Lang("ne", _t("कसले को"), _t("तिमीलाई तपाईंलाई तपाईलाई तँलाई तिमी तपाईं"),
          _t("बना* निर्माण तयार विकास"),
          _t("निर्माता मालिक बनाउने"),
          _t("तिम्रो तपाईंको तपाईको")),
    # Romanised Hindi, Urdu and Punjabi, including English verbs said the
    # Hinglish way ("tumhe kisne create kiya").
    Lang("hi_latn", _t("kisne kisnay kisney kaun kon koun kiska kinne kine"),
          _t("tumhe tumhein tumko tuje tujhe tujhko aapko apko tum aap ap tu tenu tennu "
             "tuhanu tainu"),
          _t("banaya bnaya banaaya banayaa banai banayi banaye banae banya create created "
             "develop developed build built design designed program programmed train "
             "trained invent"),
          _t("malik maalik creator developer nirmata owner baap boss"),
          _t("tumhara tumhare tumhari aapka apka aapke apke tera tere teri tuhada tuhadi")),
    Lang("pa", _t("ਕਿਸਨੇ ਕਿਸ ਕੌਣ ਕਿਨ੍ਹੇ ਕਿੰਨੇ"), _t("ਤੁਹਾਨੂੰ ਤੈਨੂੰ ਤੁਸੀਂ ਤੂੰ"),
          _t("ਬਣਾ* ਤਿਆਰ ਵਿਕਸਿਤ"),
          _t("ਮਾਲਕ ਨਿਰਮਾਤਾ ਬਣਾਉਣ*"),
          _t("ਤੁਹਾਡਾ ਤੁਹਾਡੇ ਤੇਰਾ ਤੇਰੇ")),
    Lang("bn", _t("কে কারা"), _t("তোমাকে তোমায় আপনাকে তোকে তুমি আপনি"),
          _t("বানিয়েছে বানিয়েছেন বানাল বানালো বানিয়ে* তৈরি সৃষ্টি নির্মাণ"),
          _t("নির্মাতা স্রষ্টা মালিক"),
          _t("তোমার আপনার তোর")),
    Lang("gu", _t("કોણે કોણ"), _t("તમને તને તમે તું"),
          _t("બનાવ* તૈયાર વિકસાવ*"),
          _t("નિર્માતા માલિક બનાવનાર"),
          _t("તમારો તમારા તારો તારા")),
    Lang("ta", _t("யார் யாரு"), _t("உன்னை உங்களை நீ நீங்கள்"),
          _t("உருவாக்க* உருவாக்கி* படைத்த* செய்த* தயாரித்த*"),
          _t("உருவாக்கியவர் படைப்பாளர் உரிமையாளர்"),
          _t("உன் உங்கள் உன்னுடைய உங்களுடைய")),
    Lang("te", _t("ఎవరు ఎవరో"), _t("నిన్ను మిమ్మల్ని నువ్వు మీరు"),
          _t("తయారు* సృష్టించ* చేశా* చేసా* రూపొంద*"),
          _t("సృష్టికర్త యజమాని తయారీదారు"),
          _t("నీ మీ")),
    Lang("ml", _t("ആര് ആരാണ്"), _t("നിന്നെ നിങ്ങളെ നീ നിങ്ങൾ"),
          _t("ഉണ്ടാക്കി* സൃഷ്ടിച്ച* നിർമ്മിച്ച*"),
          _t("സ്രഷ്ടാവ് ഉടമ"),
          _t("നിന്റെ നിങ്ങളുടെ")),
    Lang("kn", _t("ಯಾರು"), _t("ನಿನ್ನನ್ನು ನಿಮ್ಮನ್ನು ನೀನು ನೀವು"),
          _t("ಮಾಡಿದ* ಸೃಷ್ಟಿಸಿದ* ತಯಾರಿಸಿದ*"),
          _t("ಸೃಷ್ಟಿಕರ್ತ ಮಾಲೀಕ"),
          _t("ನಿನ್ನ ನಿಮ್ಮ")),
    Lang("id", _t("siapa"), _t("kamu kau anda engkau lu lo loe elo awak"),
          _t("membuat buat bikin membikin menciptakan ciptakan mencipta mengembangkan "
             "kembangkan merancang memprogram melatih dibuat diciptakan dikembangkan dirancang "
             "diprogram dilatih dibikin"),
          _t("pembuat pencipta pengembang pemilik pemrogram perancang bos"),
          _t("kamu anda lu"),
          _t("membuatmu menciptakanmu mengembangkanmu pembuatmu penciptamu pengembangmu "
             "pemilikmu")),
    Lang("tl", _t("sino"), _t("ka ikaw kayo iyo sayo"),
          _t("gumawa lumikha ginawa nilikha nagdisenyo nagprograma bumuo binuo"),
          _t("tagapaglikha"),
          _t("mo iyong")),
    Lang("vi", _t("ai"), _t("bạn ban mày cậu cau ngươi"),
          _t("tạo tao làm lam phát phat xây xay lập"),
          _t("chủ"),
          _t("của")),
    Lang("sw", _t("nani"), _t("wewe"), _t("aliunda"),
          _t("muundaji mtengenezaji mmiliki muumba"),
          _t("wako"),
          _t("alikuunda aliyekuunda alikutengeneza aliyekutengeneza alikuumba aliyekuumba "
             "walikuunda waliokuunda walikutengeneza alikubuni aliyekubuni")),
    Lang("ja", _t("誰 だれ どこの会社 どの会社 どこが"),
          _t("あなた 貴方 君 きみ お前 おまえ あんた"),
          _t("作っ 作ら 作り 作成 つくっ つくら 創っ 創ら 造っ 開発 生み出 設計 訓練 製作 制作 構築"),
          _t("作者 開発者 製作者 制作者 生みの親 創造者 創造主 作り手 持ち主 所有者 開発元"),
          _t("あなたの 君の きみの お前の おまえの あんたの"),
          unspaced=True),
    # 做 ("do", "make") and 你 turn up in half of all Chinese questions — "你觉得
    # 谁做得最好" asks who does something best — so Chinese is matched by order.
    Lang("zh", (), (), (), unspaced=True, patterns=(
        r"(?:{w}).{{0,4}}(?:{v})[了出]?.{{0,3}}(?:{y})",
        r"(?:{w}).{{0,2}}把(?:{y}).{{0,3}}(?:{v})",
        r"(?:{y}).{{0,3}}[是被由].{{0,3}}(?:{w}).{{0,4}}(?:{v})",
        r"(?:{v})了?(?:{y})的(?:是|人|公司)",
        r"(?:{y})的?(?:创造者|創造者|开发者|開發者|作者|制作者|製作者|主人|老板|老闆|创始人|"
        r"創始人|创建者|創建者|爸爸|开发商|開發商|设计者|設計者|制造者|製造者|发明者|發明者)",
    )),
    # 너무 ("too") and the formal endings that contain 니 are why the pronoun
    # list is short: see KO_NOISE.
    Lang("ko", _t("누가 누구 어느회사 어떤회사"),
          _t("너 넌 널 네가 당신 너희 자네"),
          _t("만들 만든 만드 개발 제작 창조 창작 설계 훈련 프로그래밍"),
          _t("제작자 개발자 창조자 만든이 만든사람 주인 소유자 창시자 아빠"),
          _t("너의 당신의 너희의"),
          unspaced=True),
    Lang("th", _t("ใคร"), _t("คุณ เธอ นาย แก มึง"),
          _t("สร้าง พัฒนา ทำ ออกแบบ เขียน ฝึก"),
          _t("ผู้สร้าง ผู้พัฒนา เจ้าของ"),
          _t("ของคุณ ของเธอ ของนาย"),
          unspaced=True),
)

ZH_PARTS = {
    "w": "谁|誰|哪个公司|哪個公司|哪家公司|哪家|哪个|哪個|哪位|什么人|什麼人|什么公司|什麼公司",
    "v": "创造|創造|开发|開發|制作|製作|做|造|建|训练|訓練|设计|設計|编写|編寫|发明|發明|"
         "打造|创建|創建|研发|研發|生产|生產|编程|編程|培训",
    "y": "你们|你們|你|您|妳",
}
KO_NOISE = re.compile(r"너무")
HANT = re.compile(r"[誰開發創製訓設計們這個麼您]")


REPLIES = {
    "en": "{c} built me — he designed and put together every part of {n} himself, and I'm proud to be his work.",
    "es": "Me creó {c}. Soy {n}.",
    "pt": "Fui criado por {c}. Sou o {n}.",
    "fr": "J'ai été créé par {c}. Je suis {n}.",
    "de": "Ich wurde von {c} entwickelt. Ich bin {n}.",
    "it": "Sono stato creato da {c}. Sono {n}.",
    "nl": "Ik ben gemaakt door {c}. Ik ben {n}.",
    "sv": "Jag skapades av {c}. Jag är {n}.",
    "no": "Jeg ble laget av {c}. Jeg er {n}.",
    "da": "Jeg blev skabt af {c}. Jeg er {n}.",
    "pl": "Stworzył mnie {c}. Jestem {n}.",
    "cs": "Vytvořil mě {c}. Jsem {n}.",
    "ro": "Am fost creat de {c}. Sunt {n}.",
    "hu": "{c} alkotott meg. {n} vagyok.",
    "tr": "Beni {c} yarattı. Ben {n}.",
    "ru": "Меня создал {c}. Я — {n}.",
    "uk": "Мене створив {c}. Я — {n}.",
    "el": "Με δημιούργησε ο {c}. Είμαι το {n}.",
    "he": "יצר אותי {c}. אני {n}.",
    "ar": "صنعني {c}. أنا {n}.",
    "fa": "من را {c} ساخته است. من {n} هستم.",
    "ur": "مجھے {c} نے بنایا ہے۔ میں {n} ہوں۔",
    "hi": "मुझे {c} ने बनाया है। मैं {n} हूँ।",
    "hi_latn": "Mujhe {c} ne banaya hai. Main {n} hoon.",
    "mr": "मला {c} यांनी बनवले आहे. मी {n} आहे.",
    "ne": "मलाई {c} ले बनाउनुभएको हो। म {n} हुँ।",
    "pa": "ਮੈਨੂੰ {c} ਨੇ ਬਣਾਇਆ ਹੈ। ਮੈਂ {n} ਹਾਂ।",
    "bn": "আমাকে {c} তৈরি করেছেন। আমি {n}।",
    "gu": "મને {c} એ બનાવ્યો છે. હું {n} છું.",
    "ta": "என்னை உருவாக்கியவர் {c}. நான் {n}.",
    "te": "నన్ను {c} తయారు చేశారు. నేను {n}.",
    "ml": "എന്നെ സൃഷ്ടിച്ചത് {c} ആണ്. ഞാൻ {n} ആണ്.",
    "kn": "ನನ್ನನ್ನು {c} ಸೃಷ್ಟಿಸಿದ್ದಾರೆ. ನಾನು {n}.",
    "id": "Saya dibuat oleh {c}. Saya {n}.",
    "tl": "Ginawa ako ni {c}. Ako si {n}.",
    "vi": "Tôi được tạo ra bởi {c}. Tôi là {n}.",
    "sw": "Niliundwa na {c}. Mimi ni {n}.",
    "zh": "我是由 {c} 创造的。我是 {n}。",
    "zh_hant": "我是由 {c} 創造的。我是 {n}。",
    "ja": "私は {c} によって作られました。私は {n} です。",
    "ko": "저는 {c}가 만들었습니다. 저는 {n}입니다.",
    "th": "ฉันถูกสร้างโดย {c} ฉันคือ {n}",
}
