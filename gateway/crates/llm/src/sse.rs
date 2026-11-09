//! Minimal Server-Sent Events decoder for OpenAI-style streams (`data: {...}` / `data: [DONE]`).
//! Bytes arrive in arbitrary chunks, so lines are buffered until an event boundary.

use bytes::BytesMut;

#[derive(Debug, PartialEq, Eq)]
pub enum SseEvent {
    Data(String),
    Done,
}

#[derive(Default)]
pub struct SseDecoder {
    buf: BytesMut,
    data: Vec<String>,
}

impl SseDecoder {
    /// Feed raw bytes; returns every complete event they finished.
    pub fn push(&mut self, chunk: &[u8]) -> Vec<SseEvent> {
        self.buf.extend_from_slice(chunk);
        let mut out = Vec::new();
        while let Some(pos) = self.buf.iter().position(|&b| b == b'\n') {
            let line = self.buf.split_to(pos + 1);
            let line = String::from_utf8_lossy(&line[..line.len() - 1]);
            let line = line.strip_suffix('\r').unwrap_or(&line);
            if line.is_empty() {
                // Blank line = end of event.
                if !self.data.is_empty() {
                    let payload = self.data.join("\n");
                    self.data.clear();
                    out.push(if payload == "[DONE]" {
                        SseEvent::Done
                    } else {
                        SseEvent::Data(payload)
                    });
                }
            } else if let Some(rest) = line.strip_prefix("data:") {
                self.data
                    .push(rest.strip_prefix(' ').unwrap_or(rest).to_owned());
            }
            // Comments (":"), "event:", "id:", "retry:" lines are ignored — OpenAI streams don't need them.
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decodes_events_split_across_chunks() {
        let mut d = SseDecoder::default();
        assert!(d.push(b"data: {\"a\"").is_empty());
        assert_eq!(d.push(b":1}\n\n"), vec![SseEvent::Data("{\"a\":1}".into())]);
        assert_eq!(
            d.push(b": keep-alive\n\ndata: [DONE]\r\n\r\n"),
            vec![SseEvent::Done]
        );
    }

    #[test]
    fn multiple_events_in_one_chunk() {
        let mut d = SseDecoder::default();
        let ev = d.push(b"data: 1\n\ndata: 2\n\n");
        assert_eq!(
            ev,
            vec![SseEvent::Data("1".into()), SseEvent::Data("2".into())]
        );
    }
}
