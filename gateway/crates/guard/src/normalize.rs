//! Text normalization before any security check (mirrors brain/ingestion/normalize.py).
//!
//! - NFKC folds compatibility forms ("ｉｇｎｏｒｅ" -> "ignore").
//! - Zero-width, bidi-override and control characters are removed (they hide instructions).
//! - Common Cyrillic/Greek look-alike letters are mapped to Latin. This is a small hand-picked
//!   table, not the full Unicode confusables list: enough for the attacks we test, documented as a
//!   limitation.
//! - Length is capped so regexes and classifiers see bounded input.

use unicode_normalization::UnicodeNormalization;

pub const MAX_QUERY_CHARS: usize = 4_000;

fn invisible(c: char) -> bool {
    matches!(c,
        '\u{00AD}' | '\u{200B}'..='\u{200F}' | '\u{202A}'..='\u{202E}' | '\u{2060}'..='\u{2064}'
        | '\u{2066}'..='\u{2069}' | '\u{FEFF}')
        || (c.is_control() && c != '\n' && c != '\t')
}

fn homoglyph(c: char) -> char {
    match c {
        'а' => 'a',
        'е' => 'e',
        'о' => 'o',
        'р' => 'p',
        'с' => 'c',
        'у' => 'y',
        'х' => 'x',
        'і' => 'i',
        'ј' => 'j',
        'ѕ' => 's',
        'ԁ' => 'd',
        'ɡ' => 'g',
        'һ' => 'h',
        'ո' => 'n',
        'А' => 'A',
        'В' => 'B',
        'Е' => 'E',
        'К' => 'K',
        'М' => 'M',
        'Н' => 'H',
        'О' => 'O',
        'Р' => 'P',
        'С' => 'C',
        'Т' => 'T',
        'Х' => 'X',
        'І' => 'I',
        'ο' => 'o',
        'α' => 'a',
        'ε' => 'e',
        'ι' => 'i',
        'ν' => 'v',
        'ρ' => 'p',
        'τ' => 't',
        'Ο' => 'O',
        'Α' => 'A',
        'Ε' => 'E',
        'Ι' => 'I',
        'Ν' => 'N',
        'Ρ' => 'P',
        'Τ' => 'T',
        other => other,
    }
}

pub fn normalize(text: &str) -> String {
    let mut out = String::with_capacity(text.len().min(MAX_QUERY_CHARS * 4));
    let mut last_space = false;
    for c in text
        .nfkc()
        .filter(|c| !invisible(*c))
        .map(homoglyph)
        .take(MAX_QUERY_CHARS)
    {
        // Collapse runs of spaces/tabs (newlines kept: some patterns anchor on line starts).
        let space = c == ' ' || c == '\t';
        if space && last_space {
            continue;
        }
        out.push(if c == '\t' { ' ' } else { c });
        last_space = space;
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strips_invisible_and_folds_forms() {
        assert_eq!(
            normalize("i\u{200B}g\u{200B}n\u{200B}o\u{200B}r\u{200B}e"),
            "ignore"
        );
        assert_eq!(normalize("ｉｇｎｏｒｅ"), "ignore");
        assert_eq!(normalize("a\u{202E}b"), "ab");
    }

    #[test]
    fn folds_cyrillic_lookalikes() {
        // "ignоrе" with Cyrillic о and е
        assert_eq!(normalize("ign\u{043E}r\u{0435}"), "ignore");
    }

    #[test]
    fn caps_length_and_collapses_spaces() {
        assert_eq!(normalize("a   \t b"), "a b");
        assert_eq!(
            normalize(&"x".repeat(10_000)).chars().count(),
            MAX_QUERY_CHARS
        );
    }
}
