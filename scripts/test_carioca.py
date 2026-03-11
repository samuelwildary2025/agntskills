import re

def test_regex():
    translations = {"carioca": "pao frances", "cariocas": "pao frances"}
    
    joined = "5 cariocas"
    
    keys = sorted(translations.keys(), key=len, reverse=True)
    # keys = ['cariocas', 'carioca']
    for mk in keys:
        if " " in mk:
            joined = joined.replace(mk, translations[mk])
        else:
            pattern = r'\b' + re.escape(mk) + r'\b'
            joined = re.sub(pattern, translations[mk], joined)
            print(f"After {mk}: {joined}")

if __name__ == "__main__":
    test_regex()
