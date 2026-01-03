# RO2 Engine Files

Tools for working with Ragnarok Online 2 engine files (Gamebryo/NIF format).

## Tools

### fix_nif_winding.py

Fix inside-out NIF models by reversing triangle winding order.

```bash
# Single file
python tools/fix_nif_winding.py model.nif

# Batch process directory
python tools/fix_nif_winding.py ./models/
```

## NIF Format

RO2 uses Gamebryo File Format version 20.6.0.0:

- **NiMesh** - Mesh geometry with stream references
- **NiDataStream** - Vertex/index buffer data
- **INDEX stream** - Triangle indices (uint16 or uint32)

## Requirements

- Python 3.x
- No external packages required
