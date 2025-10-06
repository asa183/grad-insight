import re, csv, datetime

UNIV="慶應義塾大学"; GRAD="商学研究科"
MAJOR="商業学分野 Commercial science (Marketing)"
SRC="https://www.fbc.keio.ac.jp/graduate/shougyou.html"
OUT="keio_marketing_ocr.csv"

NAME_RE=r"[一-龥々〆ヵヶ]{1,4}[ \u3000]+[一-龥々〆ヵヶ]{1,4}"

text=open("ocr_debug.txt","r",encoding="utf-8").read()
tail=text.split("教員紹介",1)[1] if "教員紹介" in text else text
lines=[ln.strip() for ln in tail.splitlines() if ln.strip()]
titles={"教授","准教授","特任教授","助教","担当者"}

names=[]
i=0
while i<len(lines):
    if any(t in lines[i] for t in titles) and i+1<len(lines):
        nm=lines[i+1]
        m=re.search(NAME_RE, nm)
        if m:
            names.append(m.group(0).strip()); i+=2; continue
    i+=1

# romanization pair backfill
for j in range(2,len(lines)):
    if re.fullmatch(r"[A-Za-z\-]{2,}", lines[j-1]) and re.fullmatch(r"[A-Za-z\-]{2,}", lines[j]):
        cand=lines[j-2]
        kan=re.sub(r"[^一-龥々〆ヵヶ]", "", cand)
        if 3<=len(kan)<=6:
            nm=kan[:2]+" "+kan[2:]
            if nm not in names:
                names.append(nm)

rows=[{"大学名":UNIV,"研究科":GRAD,"専攻名":MAJOR,
       "氏名（漢字）":n,
       "研究テーマ（スラッシュ区切り）":"",
       "個人ページURL":"","出典URL":SRC,
       "取得日時":datetime.date.today().isoformat()} for n in names]

cols=["大学名","研究科","専攻名","氏名（漢字）","研究テーマ（スラッシュ区切り）","個人ページURL","出典URL","取得日時"]
with open(OUT,"w",newline="",encoding="utf-8") as f:
    w=csv.DictWriter(f,fieldnames=cols); w.writeheader(); w.writerows(rows)
print(f"names={len(names)} -> {OUT}")
