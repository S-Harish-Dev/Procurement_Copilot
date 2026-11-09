//! Request-path PII masking: regex fast path with validators (IBAN mod-97, card Luhn) and
//! reversible placeholders. Ingestion uses Presidio + NER; queries only need the structured
//! types, and must be masked in microseconds before anything leaves the process (Jev, LLMs, logs).

use std::collections::HashMap;
use std::sync::OnceLock;

use regex::Regex;

/// Placeholder -> original value, kept only in the request context. Unmasking the answer is
/// allowed only for roles OPA permits (`view_pii`); logs and traces always see masked text.
#[derive(Debug, Default, Clone)]
pub struct PiiMap {
    to_original: HashMap<String, String>,
    to_placeholder: HashMap<(String, String), String>,
    counts: HashMap<String, usize>,
}

impl PiiMap {
    fn placeholder(&mut self, kind: &str, value: &str) -> String {
        let key = (kind.to_owned(), value.to_owned());
        if let Some(p) = self.to_placeholder.get(&key) {
            return p.clone();
        }
        let n = self.counts.entry(kind.to_owned()).or_default();
        *n += 1;
        let p = format!("⟨{kind}_{n}⟩");
        self.to_placeholder.insert(key, p.clone());
        self.to_original.insert(p.clone(), value.to_owned());
        p
    }

    pub fn is_empty(&self) -> bool {
        self.to_original.is_empty()
    }

    pub fn kinds(&self) -> Vec<String> {
        let mut k: Vec<String> = self.counts.keys().cloned().collect();
        k.sort();
        k
    }

    pub fn unmask(&self, text: &str) -> String {
        let mut out = text.to_owned();
        for (placeholder, original) in &self.to_original {
            out = out.replace(placeholder, original);
        }
        out
    }
}

struct Rx {
    email: Regex,
    iban: Regex,
    card: Regex,
    ssn: Regex,
    ein: Regex,
    phone: Regex,
}

fn rx() -> &'static Rx {
    static R: OnceLock<Rx> = OnceLock::new();
    R.get_or_init(|| Rx {
        email: Regex::new(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}").expect("re"),
        iban: Regex::new(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b").expect("re"),
        card: Regex::new(r"\b\d(?:[ -]?\d){12,18}\b").expect("re"),
        ssn: Regex::new(r"\b\d{3}-\d{2}-\d{4}\b").expect("re"),
        // EIN only with a context word just before it, so clause numbers like 52-2091234 survive.
        ein: Regex::new(r"(?i)\b(?:ein|tin|tax(?:payer)? id(?:entification)?(?: number)?)[:#\s]{1,3}(\d{2}-\d{7})\b").expect("re"),
        phone: Regex::new(r"(?:\+\d{1,3}[\s.-]?)?\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b").expect("re"),
    })
}

fn iban_valid(candidate: &str) -> bool {
    let s: String = candidate.chars().filter(|c| !c.is_whitespace()).collect();
    if !(15..=34).contains(&s.len()) {
        return false;
    }
    let rearranged = format!("{}{}", &s[4..], &s[..4]);
    let mut rem: u32 = 0;
    for c in rearranged.chars() {
        let digits = match c {
            '0'..='9' => c.to_digit(10).map(|d| d.to_string()),
            'A'..='Z' => Some((c as u32 - 'A' as u32 + 10).to_string()),
            _ => None,
        };
        let Some(d) = digits else { return false };
        for ch in d.chars() {
            rem = (rem * 10 + ch.to_digit(10).unwrap_or(0)) % 97;
        }
    }
    rem == 1
}

fn luhn_valid(candidate: &str) -> bool {
    let digits: Vec<u32> = candidate.chars().filter_map(|c| c.to_digit(10)).collect();
    if !(13..=19).contains(&digits.len()) {
        return false;
    }
    let sum: u32 = digits
        .iter()
        .rev()
        .enumerate()
        .map(|(i, &d)| {
            if i % 2 == 1 {
                let x = d * 2;
                if x > 9 { x - 9 } else { x }
            } else {
                d
            }
        })
        .sum();
    sum.is_multiple_of(10)
}

/// Masks structured PII in `text`, extending `map` (so the same value keeps its placeholder
/// across the query and the answer).
pub fn mask_pii(text: &str, map: &mut PiiMap) -> String {
    let r = rx();
    let mut out = r
        .email
        .replace_all(text, |c: &regex::Captures| map.placeholder("EMAIL", &c[0]))
        .into_owned();
    out = r
        .iban
        .replace_all(&out, |c: &regex::Captures| {
            if iban_valid(&c[0]) {
                map.placeholder("IBAN", &c[0])
            } else {
                c[0].to_owned()
            }
        })
        .into_owned();
    out = r
        .card
        .replace_all(&out, |c: &regex::Captures| {
            if luhn_valid(&c[0]) {
                map.placeholder("CARD", &c[0])
            } else {
                c[0].to_owned()
            }
        })
        .into_owned();
    out = r
        .ssn
        .replace_all(&out, |c: &regex::Captures| map.placeholder("SSN", &c[0]))
        .into_owned();
    out = r
        .ein
        .replace_all(&out, |c: &regex::Captures| {
            let whole = &c[0];
            let num = &c[1];
            whole.replace(num, &map.placeholder("TAX_ID", num))
        })
        .into_owned();
    r.phone
        .replace_all(&out, |c: &regex::Captures| map.placeholder("PHONE", &c[0]))
        .into_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn masks_and_unmasks_consistently() {
        let mut m = PiiMap::default();
        let t = "Mail jane.doe@vendor.com or jane.doe@vendor.com, call +1 415-555-0134. IBAN DE89 3704 0044 0532 0130 00.";
        let masked = mask_pii(t, &mut m);
        assert!(
            !masked.contains("jane.doe")
                && !masked.contains("555-0134")
                && !masked.contains("DE89"),
            "{masked}"
        );
        assert_eq!(masked.matches("⟨EMAIL_1⟩").count(), 2);
        assert_eq!(m.unmask(&masked), t);
    }

    #[test]
    fn validators_avoid_false_positives() {
        let mut m = PiiMap::default();
        // Fails mod-97 / Luhn / has no tax context: must stay.
        let t = "Ref DE00 1234 5678 9012 3456 78, order 4111 1111 1111 1112, see clause 52-2091234 and FAR 52.212-4.";
        assert_eq!(mask_pii(t, &mut m), t);
        assert!(m.is_empty());
    }

    #[test]
    fn valid_card_and_tax_id_masked() {
        let mut m = PiiMap::default();
        let out = mask_pii("card 4111 1111 1111 1111, EIN: 12-3456789", &mut m);
        assert!(
            out.contains("⟨CARD_1⟩") && out.contains("⟨TAX_ID_1⟩"),
            "{out}"
        );
    }
}
