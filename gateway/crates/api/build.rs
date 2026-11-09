// Generates the brain gRPC client from the shared contract (proto/brain.proto). Needs `protoc`.
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let proto = "../../../proto/brain.proto";
    println!("cargo:rerun-if-changed={proto}");
    tonic_prost_build::configure()
        .build_server(false)
        .compile_protos(&[proto], &["../../../proto"])?;
    Ok(())
}
