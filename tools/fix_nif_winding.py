#!/usr/bin/env python3
"""
Fix inside-out NIF models by reversing triangle winding order.
Properly parses NIF 20.6 format to find INDEX streams.
Based on Noesis addon parsing logic.
"""

import struct
import sys
from pathlib import Path


def nif_version(major, minor, patch, internal):
    return (major << 24) | (minor << 16) | (patch << 8) | internal


class NifParser:
    def __init__(self, data):
        self.data = bytearray(data)
        self.pos = 0
        self.file_ver = 0
        self.user_ver = 0
        self.num_objects = 0
        self.type_names = []
        self.object_types = []
        self.object_sizes = []
        self.object_offsets = []
        self.string_table = []
        self.modified = False

    def read_byte(self):
        val = self.data[self.pos]
        self.pos += 1
        return val

    def read_ushort(self):
        val = struct.unpack_from('<H', self.data, self.pos)[0]
        self.pos += 2
        return val

    def read_uint(self):
        val = struct.unpack_from('<I', self.data, self.pos)[0]
        self.pos += 4
        return val

    def read_int(self):
        val = struct.unpack_from('<i', self.data, self.pos)[0]
        self.pos += 4
        return val

    def read_float(self):
        val = struct.unpack_from('<f', self.data, self.pos)[0]
        self.pos += 4
        return val

    def read_string(self, length):
        s = self.data[self.pos:self.pos + length]
        self.pos += length
        try:
            return s.decode('utf-8').rstrip('\x00')
        except:
            return s.decode('latin-1').rstrip('\x00')

    def parse_header(self):
        """Parse NIF header."""
        header_end = self.data.find(b'\n')
        if header_end == -1:
            return False
        header_line = self.data[:header_end].decode('ascii')
        if 'Gamebryo File Format' not in header_line:
            return False

        print(f"  {header_line}")
        self.pos = header_end + 1

        self.file_ver = self.read_uint()
        ver_maj = (self.file_ver >> 24) & 0xFF
        ver_min = (self.file_ver >> 16) & 0xFF

        if self.file_ver >= nif_version(20, 0, 0, 3):
            self.is_little_endian = self.read_byte() > 0

        if self.file_ver >= nif_version(10, 0, 1, 8):
            self.user_ver = self.read_uint()

        self.num_objects = self.read_int()
        if self.num_objects <= 0:
            return False
        print(f"  Objects: {self.num_objects}")

        if self.user_ver == nif_version(0, 0, 0, 12) and self.file_ver == nif_version(20, 2, 0, 7):
            self.user_ver_2 = self.read_uint()

        return True

    def parse_metadata(self):
        """Parse metadata - only for version >= 20.9.0.1"""
        if self.file_ver >= nif_version(20, 9, 0, 1):
            meta_size = self.read_uint()
            if meta_size > 0:
                self.pos += meta_size
        return True

    def parse_type_names_and_indices(self):
        """Parse type names and object type indices."""
        num_types = self.read_ushort()
        self.type_names = []
        for i in range(num_types):
            str_len = self.read_uint()
            type_name = self.read_string(str_len)
            self.type_names.append(type_name)

        self.object_types = []
        for i in range(self.num_objects):
            type_idx = self.read_ushort()
            if self.file_ver >= nif_version(20, 2, 0, 5):
                type_idx &= ~32768
            self.object_types.append(self.type_names[type_idx])
        return True

    def parse_object_sizes(self):
        """Parse object sizes - for version >= 20.2.0.5"""
        if self.file_ver >= nif_version(20, 2, 0, 5):
            self.object_sizes = []
            for i in range(self.num_objects):
                self.object_sizes.append(self.read_uint())
        return True

    def parse_string_table(self):
        """Parse string table - for version >= 20.1.0.1"""
        if self.file_ver >= nif_version(20, 1, 0, 1):
            num_strings = self.read_uint()
            max_string_size = self.read_uint()
            self.string_table = []
            for i in range(num_strings):
                str_len = self.read_uint()
                if str_len > 0:
                    self.string_table.append(self.read_string(str_len))
                else:
                    self.string_table.append("")
            print(f"  String table: {num_strings} strings")
        return True

    def parse_object_groups(self):
        """Parse object groups - for version >= 5.0.0.6"""
        if self.file_ver >= nif_version(5, 0, 0, 6):
            num_groups = self.read_uint()
            for i in range(num_groups):
                self.read_uint()
        return True

    def find_index_streams_in_mesh(self, mesh_idx, index_str_idx):
        """
        Parse NiMesh to find INDEX stream references.
        Returns list of (stream_id, element_info) tuples.
        """
        streams = []
        start_pos = self.object_offsets[mesh_idx]
        end_pos = start_pos + self.object_sizes[mesh_idx]

        # Search for INDEX string index in element descriptors
        # Element desc structure: [stringIndex:4][elementIndex:4]
        index_pattern = struct.pack('<I', index_str_idx)

        search_pos = start_pos
        while search_pos < end_pos - 20:
            idx = self.data.find(index_pattern, search_pos, end_pos)
            if idx == -1:
                break

            # Verify this looks like an element descriptor
            elem_idx_val = struct.unpack_from('<I', self.data, idx + 4)[0]
            if elem_idx_val >= 10:
                search_pos = idx + 1
                continue

            # Work backwards to find numElementDescs
            found = False
            for back in range(4, 100, 4):
                num_pos = idx - back
                if num_pos < start_pos:
                    break

                num_elems = struct.unpack_from('<I', self.data, num_pos)[0]
                if not (1 <= num_elems <= 10):
                    continue

                # Validate all element descriptors
                valid = True
                elem_start = num_pos + 4
                for e in range(num_elems):
                    e_pos = elem_start + e * 8
                    if e_pos + 8 > end_pos:
                        valid = False
                        break
                    e_str = struct.unpack_from('<I', self.data, e_pos)[0]
                    e_idx = struct.unpack_from('<I', self.data, e_pos + 4)[0]
                    if e_str >= len(self.string_table) or e_idx >= 10:
                        valid = False
                        break

                if not valid:
                    continue

                # Found numElementDescs. Now find streamLinkID.
                # Before numElementDescs: submeshRegionMap entries (ushort each)
                # Before that: numSubmeshRegionMapEntries (ushort)
                # Before that: instanced (byte)
                # Before that: streamLinkID (uint)

                # Try different positions for streamLinkID
                for link_offset in range(7, 50):
                    link_pos = num_pos - link_offset
                    if link_pos < start_pos:
                        break

                    link_id = struct.unpack_from('<I', self.data, link_pos)[0]
                    if 0 <= link_id < self.num_objects:
                        obj_type = self.object_types[link_id]
                        if obj_type.startswith("NiDataStream"):
                            streams.append((link_id, elem_idx_val))
                            found = True
                            break

                if found:
                    break

            search_pos = idx + 1

        return streams

    def fix_datastream_winding(self, stream_idx):
        """Reverse triangle winding in NiDataStream INDEX buffer."""
        self.pos = self.object_offsets[stream_idx]

        # NiDataStream structure (NO name field for this type):
        # - streamSize (uint)
        # - streamClone (uint)
        # - numRegions (uint)
        # - regions[] ((baseIdx, count) pairs)
        # - numElements (uint)
        # - elements[] (packed format uint each)
        # - streamData (streamSize bytes)
        # - streamable (byte)

        stream_size = self.read_uint()
        stream_clone = self.read_uint()
        num_regions = self.read_uint()

        regions = []
        for r in range(num_regions):
            base_idx = self.read_uint()
            count = self.read_uint()
            regions.append((base_idx, count))

        num_elements = self.read_uint()

        elem_stride = 0
        index_size = 2  # Default ushort

        for e in range(num_elements):
            elem_data = self.read_uint()
            # Packed: (count << 16) | (size << 8) | dataType
            elem_count = (elem_data >> 16) & 0xFF
            elem_size = (elem_data >> 8) & 0xFF
            elem_type = elem_data & 0xFF

            if e == 0:
                index_size = elem_size
                # Data type 5 or 7 = USHORT (16-bit), 11 = UINT (32-bit)
                if elem_type in (10, 11):
                    index_size = 4

            elem_stride += elem_count * elem_size

        data_start = self.pos

        print(f"      Stream {stream_idx}: size={stream_size}, regions={len(regions)}, index_size={index_size}")

        # Reverse winding for all triangles in all regions
        total_triangles = 0

        for region_idx, (base_idx, count) in enumerate(regions):
            if index_size == 2:
                num_indices = count
                if num_indices % 3 != 0:
                    continue
                num_triangles = num_indices // 3
                region_offset = data_start + base_idx * elem_stride

                for t in range(num_triangles):
                    offset = region_offset + t * 6
                    if offset + 6 > len(self.data):
                        break
                    a, b, c = struct.unpack_from('<HHH', self.data, offset)
                    struct.pack_into('<HHH', self.data, offset, a, c, b)

                total_triangles += num_triangles

            elif index_size == 4:
                num_indices = count
                if num_indices % 3 != 0:
                    continue
                num_triangles = num_indices // 3
                region_offset = data_start + base_idx * elem_stride

                for t in range(num_triangles):
                    offset = region_offset + t * 12
                    if offset + 12 > len(self.data):
                        break
                    a, b, c = struct.unpack_from('<III', self.data, offset)
                    struct.pack_into('<III', self.data, offset, a, c, b)

                total_triangles += num_triangles

        if total_triangles > 0:
            print(f"      Reversed winding for {total_triangles} triangles")
            self.modified = True

    def process_all_meshes(self):
        """Find and process all NiMesh INDEX streams."""
        try:
            index_str_idx = self.string_table.index("INDEX")
            print(f"  'INDEX' at string table index {index_str_idx}")
        except ValueError:
            print("  No 'INDEX' string in string table")
            return

        processed_streams = set()

        for i in range(self.num_objects):
            if self.object_types[i] != "NiMesh":
                continue

            streams = self.find_index_streams_in_mesh(i, index_str_idx)
            for stream_id, elem_idx in streams:
                if stream_id in processed_streams:
                    continue
                processed_streams.add(stream_id)

                print(f"    Mesh {i} -> INDEX stream {stream_id}")
                self.fix_datastream_winding(stream_id)

        print(f"  Processed {len(processed_streams)} INDEX streams")

    def parse_and_fix(self):
        """Main entry point."""
        if not self.parse_header():
            return False

        if not self.parse_metadata():
            return False

        if self.file_ver >= nif_version(5, 0, 0, 1):
            if not self.parse_type_names_and_indices():
                return False

        if not self.parse_object_sizes():
            return False

        if not self.parse_string_table():
            return False

        if not self.parse_object_groups():
            return False

        # Build object offset table
        objects_start = self.pos
        self.object_offsets = []
        current_pos = objects_start
        for i in range(self.num_objects):
            self.object_offsets.append(current_pos)
            current_pos += self.object_sizes[i]

        # Count relevant types
        mesh_count = sum(1 for t in self.object_types if t == "NiMesh")
        stream_count = sum(1 for t in self.object_types if t.startswith("NiDataStream"))
        print(f"  NiMesh: {mesh_count}, NiDataStream*: {stream_count}")

        self.process_all_meshes()

        return self.modified


def fix_nif_file(input_path, output_path=None):
    """Fix a single NIF file."""
    if output_path is None:
        output_path = input_path

    with open(input_path, 'rb') as f:
        data = f.read()

    parser = NifParser(data)
    modified = parser.parse_and_fix()

    if modified:
        with open(output_path, 'wb') as f:
            f.write(parser.data)
        print(f"  Saved: {output_path}")
        return True
    else:
        print(f"  No changes made")
        return False


def main():
    if len(sys.argv) < 2:
        print("Usage: fix_nif_winding.py <file.nif> [output.nif]")
        print("       fix_nif_winding.py <directory>")
        sys.exit(1)

    path = Path(sys.argv[1])

    if path.is_file():
        output = sys.argv[2] if len(sys.argv) > 2 else str(path)
        print(f"Fixing: {path}")
        fix_nif_file(str(path), output)

    elif path.is_dir():
        nif_files = sorted(path.glob("*.nif"))
        print(f"Found {len(nif_files)} NIF files in {path}\n")

        success = 0
        for nif_file in nif_files:
            print(f"Fixing: {nif_file.name}")
            if fix_nif_file(str(nif_file)):
                success += 1
            print()

        print(f"Done: {success} fixed, {len(nif_files) - success} unchanged")

    else:
        print(f"Error: Path not found: {path}")
        sys.exit(1)


if __name__ == "__main__":
    main()
