Import("env", "projenv")

def after_firmware_upload(source, target, env):
    print("\n=== Building filesystem image ===")
    projenv.Execute("pio run --target buildfs --environment " + env.get("PIOENV"))
    
    print("\n=== Uploading filesystem image ===")
    projenv.Execute("pio run --target uploadfs --environment " + env.get("PIOENV"))

env.AddPostAction("upload", after_firmware_upload)