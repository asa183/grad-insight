import re, csv, sys, datetime, cv2
import numpy as np
import pytesseract
from pytesseract import Output
from PIL import Image

UNIV="慶應義塾大学"; GRAD="商学研究科"
MAJOR="商業学分野 Commercial science (Marketing)"
SRC="https://www.fbc.keio.ac.jp/graduate/shougyou.html"
OUT="keio_marketing_ocr.csv"

NAME_RE=r"[一-龥々〆ヵヶ]{1,3}[ \u3000]+[一-龥々〆ヵヶ]{1,3}"
ROMAN_RE=r"[A-Za-z]{2,}(?:[ -][A-Za-z\-]{2,})+"

def normalize_themes(s:str)->str:
    s=re.sub(r"[（）\(\)\[\]【】]+"," ",s)
    parts=re.split(r"[、，,/／・\n]+",s)
    cleaned=[]
    for p in parts:
        p=p.strip(" 　")
        if not p: continue
        # 英数字が混じる行や長文は除外
        if re.search(r"[A-Za-z0-9]", p):
            continue
        if len(p)>20:
            continue
        if not re.fullmatch(r"[一-龥々〆ヵヶぁ-んァ-ンー・]+", p):
            continue
        cleaned.append(p)
    return " / ".join(cleaned[:12])

def ocr_text(path:str)->str:
    img=cv2.imread(path); 
    if img is None: raise SystemExit("画像が読めません: "+path)
    h,w=img.shape[:2]
    if max(h,w)<1600: img=cv2.resize(img,(int(w*1.6),int(h*1.6)))
    gray=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
    gray=cv2.threshold(gray,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)[1]
    pil=Image.fromarray(gray)
    return pytesseract.image_to_string(pil, lang="jpn+eng")

def ocr_data(path:str):
    img=cv2.imread(path)
    H,W=img.shape[:2]
    if max(H,W)<1600:
        img=cv2.resize(img,(int(W*1.6),int(H*1.6)))
    gray=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
    gray=cv2.threshold(gray,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)[1]
    df=pytesseract.image_to_data(gray, lang="jpn+eng", output_type=Output.DATAFRAME, config="--psm 6")
    df=df.dropna(subset=["text"])  # keep only text rows
    df=df[df["text"].astype(str).str.strip()!=""]
    return gray, df

def extract_records(text:str, img_path:str|None=None):
    blocks=[b.strip() for b in re.split(r"\n\s*\n",text) if b.strip()]
    recs=[]
    for b in blocks:
        # ブロックにローマ字氏名がある（表の氏名欄の特徴）ことを前提にする
        if not re.search(ROMAN_RE, b):
            continue
        m=re.search(NAME_RE,b)
        if not m: 
            # ゆるめの2語漢字
            cand=re.findall(r"[一-龥々〆ヵヶ]{1,4}[ \u3000][一-龥々〆ヵヶ]{1,4}",b)
            if not cand: continue
            name=cand[0].strip()
        else:
            name=m.group(0).strip()

        theme=""
        KEYWORDS=["マーケティング","消費者","流通","統計","イノベーション","サイエンス","計量","リサーチ"]
        for line in b.splitlines():
            L=line.strip()
            if any(k in L for k in KEYWORDS):
                theme=normalize_themes(L)
                if theme:
                    break

        # OCRではリンクは基本拾えない→空欄
        # 出版情報などのノイズを排除
        NG=["書房","Journal","ジャーナル","Vol.","pp."]
        if any(x in b for x in NG):
            continue
        if theme:
            recs.append({
                "大学名":UNIV,"研究科":GRAD,"専攻名":MAJOR,
                "氏名（漢字）":name,
                "研究テーマ（スラッシュ区切り）":theme,
                "個人ページURL":"", "出典URL":SRC,
                "取得日時":datetime.date.today().isoformat()
            })
    # 追加: 位置情報から列抽出（より堅牢）
    if img_path:
        try:
            gray, df = ocr_data(img_path)
            H,W = gray.shape[:2]
            # 列境界の推定
            spec = df[df["text"].astype(str).str.contains("専門分野", na=False)]
            works = df[df["text"].astype(str).str.contains("主要著作", na=False)]
            x_spec = int(spec.iloc[0].left) if not spec.empty else int(W*0.35)
            x_works = int(works.iloc[0].left) if not works.empty else int(W*0.62)

            # 名前候補: ローマ字行の直上にある漢字2語を優先
            lines=[]
            for (b,p,l), g in df.groupby(["block_num","par_num","line_num"]):
                g2=g[(g.left+g.width)<=x_spec-10]
                if g2.empty:
                    continue
                text=" ".join(map(str, g2.text.tolist()))
                ymid=int((g2.top.min()+ (g2.top+g2.height).max())/2)
                lines.append({"text":text, "ymid":ymid})

            name_lines=[]
            for idx, ln in enumerate(lines):
                if re.search(r"[A-Za-z]", ln["text"]):
                    # 探索窓: 直上60px以内
                    cand=[x for x in lines[max(0,idx-3):idx] if (ln["ymid"]-x["ymid"])<=80]
                    cand=list(reversed(cand))
                    name=""
                    for c in cand:
                        if "教授" in c["text"] or "准教授" in c["text"]:
                            continue
                        m=re.search(NAME_RE, c["text"]) 
                        if m:
                            name=m.group(0).strip(); break
                    if name:
                        name_lines.append({"name":name, "ymid":ln["ymid"]})
            name_lines=sorted(name_lines, key=lambda x:x["ymid"])

            # 各名前のY帯に対して、専門分野列の語を集める
            for i,ln in enumerate(name_lines):
                y0 = name_lines[i-1]["ymid"] if i>0 else max(0, ln["ymid"]-40)
                y1 = name_lines[i+1]["ymid"] if i+1<len(name_lines) else H
                g = df[(df.top>=y0)&(df.top<=y1)&(df.left>=x_spec)&(df.left<=x_works-5)]
                words=[t for t in g.text.tolist() if isinstance(t,str) and t.strip()]
                theme=normalize_themes(" ".join(words))
                recs.append({
                    "大学名":UNIV,"研究科":GRAD,"専攻名":MAJOR,
                    "氏名（漢字）":ln["name"],
                    "研究テーマ（スラッシュ区切り）":theme,
                    "個人ページURL":"", "出典URL":SRC,
                    "取得日時":datetime.date.today().isoformat()
                })
        except Exception:
            pass

    # フォールバック: 「教員紹介」節から職名→氏名の並びで抽出（テーマは空欄でも出力）
    if not recs:
        try:
            if "教員紹介" in text:
                tail=text.split("教員紹介",1)[1]
            else:
                tail=text
            lines=[ln.strip() for ln in tail.splitlines() if ln.strip()]
            titles={"教授","准教授","特任教授","助教","担当者"}
            dbg=[]
            i=0
            while i<len(lines):
                if any(t in lines[i] for t in titles) and i+1<len(lines):
                    nm=lines[i+1]
                    m=re.search(NAME_RE, nm)
                    if m:
                        name=m.group(0).strip()
                        recs.append({
                            "大学名":UNIV,"研究科":GRAD,"専攻名":MAJOR,
                            "氏名（漢字）":name,
                            "研究テーマ（スラッシュ区切り）":"",
                            "個人ページURL":"", "出典URL":SRC,
                            "取得日時":datetime.date.today().isoformat()
                        })
                        dbg.append(name)
                        i+=2
                        continue
                i+=1
            # ローマ字2行の直前にある漢字行も拾う
            for j in range(2, len(lines)):
                if re.fullmatch(r"[A-Za-z\-]{2,}", lines[j-1]) and re.fullmatch(r"[A-Za-z\-]{2,}", lines[j]):
                    cand=lines[j-2]
                    kan=re.sub(r"[^一-龥々〆ヵヶ]", "", cand)
                    if 3 <= len(kan) <= 6:
                        nm=kan[:2]+" "+kan[2:]
                        if not any(r["氏名（漢字）"]==nm for r in recs):
                            recs.append({
                                "大学名":UNIV,"研究科":GRAD,"専攻名":MAJOR,
                                "氏名（漢字）":nm,
                                "研究テーマ（スラッシュ区切り）":"",
                                "個人ページURL":"", "出典URL":SRC,
                                "取得日時":datetime.date.today().isoformat()
                            })
                            dbg.append(nm)
            # debug dump
            try:
                with open("ocr_names_debug.txt","w",encoding="utf-8") as nf:
                    nf.write("\n".join(dbg))
            except Exception:
                pass
        except Exception:
            pass

    # さらにフォールバック: 氏名欄だけを切り出して名前を列挙（テーマは空欄）
    if not recs and img_path:
        try:
            gray, df = ocr_data(img_path)
            H,W = gray.shape[:2]
            th = df[df["text"].astype(str).str.contains("専門分野", na=False)]
            sec = df[df["text"].astype(str).str.contains("教員紹介", na=False)]
            namehdr = df[df["text"].astype(str).str.contains("担当者", na=False)]
            x_theme = int(th.iloc[0].left) if not th.empty else int(W*0.35)
            x_left  = int(namehdr.iloc[0].left) - 10 if not namehdr.empty else int(W*0.18)
            y_top   = int(sec.iloc[0].top) if not sec.empty else 0
            crop = gray[y_top:H, max(0,x_left):max(0,x_theme-10)]
            txt = pytesseract.image_to_string(crop, lang="jpn", config="--psm 6")
            names=set()
            # スペースあり
            for m in re.findall(NAME_RE, txt):
                names.add(m)
            # スペースなし（4-6連続漢字）
            for m in re.findall(r"[一-龥々〆ヵヶ]{4,6}", txt):
                # 2文字+残りで分割
                nm=m[:2]+" "+m[2:]
                names.add(nm)
            for name in sorted(names):
                recs.append({
                    "大学名":UNIV,"研究科":GRAD,"専攻名":MAJOR,
                    "氏名（漢字）":name,
                    "研究テーマ（スラッシュ区切り）":"",
                    "個人ページURL":"", "出典URL":SRC,
                    "取得方法":"OCR(Tesseract)","取得日時":datetime.date.today().isoformat()
                })
        except Exception:
            pass

    # 重複名をマージ（テーマ結合）
    merged={}
    for r in recs:
        k=r["氏名（漢字）"]
        if k not in merged: merged[k]=r
        else:
            a=merged[k]["研究テーマ（スラッシュ区切り）"]; b=r["研究テーマ（スラッシュ区切り）"]
            if b and b not in a:
                merged[k]["研究テーマ（スラッシュ区切り）"]=" / ".join([x for x in [a,b] if x])
    return list(merged.values())

def main(img_path:str):
    text=ocr_text(img_path)
    # debug dump
    try:
        with open("ocr_debug.txt","w",encoding="utf-8") as df:
            df.write(text)
    except Exception:
        pass
    recs=extract_records(text, img_path)
    cols=["大学名","研究科","専攻名","氏名（漢字）","研究テーマ（スラッシュ区切り）","個人ページURL","出典URL","取得日時"]
    with open(OUT,"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=cols); w.writeheader(); w.writerows(recs)
    print(f"書き出し: {OUT}  行数={len(recs)}")

if __name__=="__main__":
    if len(sys.argv)<2:
        print("使い方: python ocr_keio.py <screenshot.png>"); sys.exit(1)
    main(sys.argv[1])
