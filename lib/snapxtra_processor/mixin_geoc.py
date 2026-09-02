"""Terrain correction and GeoTIFF export"""

import os
import glob
import subprocess
import shutil
import json
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET

class GeocMixin:

    def convert_datetime_to_yyyymmdd(self, dt):
        """Convert datetime to YYYYMMDD string"""
        return dt.strftime('%Y%m%d')
    
    def find_matching_unwphase_band(self, unwphase_bands, folder_date1, folder_date2):
        folder_dt1 = datetime.strptime(folder_date1, '%Y%m%d')
        folder_dt2 = datetime.strptime(folder_date2, '%Y%m%d')
        
        for band in unwphase_bands:
            band_dt1, band_dt2 = self.parse_date_from_band_name(band)
            if band_dt1 and band_dt2:
                if (band_dt1 == folder_dt1 and band_dt2 == folder_dt2):
                    return band
        
        return None
    
    def get_coh_band_from_unwphase(self, unwphase_band):
        return unwphase_band.replace('UnwPhase_ifg_', 'coh_')
    
    def copy_metadata_info_to_geoc(self, geoc_dir):
        """Copy baselines, metadata, and network plot to GEOC."""
        metadata_info_dir = os.path.join(self.proj_root, 'metadata_info')
        
        network_file = 'network_sbas.png'
        
        for name in ['baselines', 'metadata', network_file]:
            src = os.path.join(metadata_info_dir, name)
            dst = os.path.join(geoc_dir, name)
            if os.path.exists(src):
                shutil.copy(src, dst)
                print(f"  ✓ Copied {name} to {dst}")
            else:
                print(f"  ⚠ {name} not found: {src}")
    
    def generate_png_from_geotiff(self, tif_path):
        try:
            import rasterio
            import numpy as np
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            from rasterio.transform import rowcol
            from shapely import wkt
            
            png_path = os.path.splitext(tif_path)[0] + ".png"
            clip_roi = str(self.config.get('clip_roi', '')).strip()
            
            if os.path.exists(png_path) and not clip_roi:
                return png_path
            
            basename = os.path.basename(tif_path).lower()
            
            if any(k in basename for k in ["phase", "xphase", "yphase", "unwrap", "unw", "los"]):
                cmap = "seismic"
            elif any(k in basename for k in ["im_", "re_"]):
                cmap = "seismic"
            elif "corr" in basename or "cc" in basename or "coh" in basename:
                cmap = "hot"
            elif any(k in basename for k in ["amp", "display", "mli"]):
                cmap = "gray"
            else:
                cmap = "seismic"
            
            with rasterio.open(tif_path) as src:
                data = src.read(1).astype(float)
                nodata = src.nodata
                transform = src.transform
                width = src.width
                height = src.height
            
            if nodata is not None:
                data = np.ma.masked_where(data == nodata, data)
            
            data = np.ma.masked_invalid(data)
            
            arr = data.filled(np.nan)
            vmin = np.nanpercentile(arr, 2)
            vmax = np.nanpercentile(arr, 98)
            
            cmap = plt.get_cmap(cmap).copy()
            cmap.set_bad(color=(0, 0, 0, 0))
            
            fig = plt.figure(figsize=(6, 6))
            plt.imshow(data, cmap=cmap, vmin=vmin, vmax=vmax) 

            if clip_roi:
                try:
                    clip_roi_wkt = self.normalize_clip_roi(clip_roi)
                    
                    roi_geom = wkt.loads(clip_roi_wkt)
                    if roi_geom.geom_type == 'Polygon':
                        polygons = [roi_geom]
                    elif roi_geom.geom_type == 'MultiPolygon':
                        polygons = list(roi_geom.geoms)
                    else:
                        polygons = []
                        print(f"    WARNING: clip_roi geometry type '{roi_geom.geom_type}' is not Polygon/MultiPolygon")

                    for poly in polygons:
                        xs, ys = poly.exterior.coords.xy
                        rows, cols = [], []
                        for lon, lat in zip(xs, ys):
                            r, c = rowcol(transform, lon, lat)
                            r = int(np.clip(r, 0, height - 1))
                            c = int(np.clip(c, 0, width - 1))
                            rows.append(r)
                            cols.append(c)

                        if rows and cols:
                            plt.plot(cols, rows, color='red', linewidth=2)

                except Exception as roi_err:
                    print(f"    WARNING: Failed to overlay clip_roi on {os.path.basename(tif_path)}: {roi_err}")

            plt.axis("off")
            
            plt.savefig(png_path, dpi=200, bbox_inches="tight", pad_inches=0, transparent=True)
            plt.close(fig)
            
            return png_path
            
        except Exception as e:
            print(f"    WARNING: PNG generation failed for {os.path.basename(tif_path)}: {e}")
            return None
    
    def copy_geoc_png_previews(self):
        """Copy all PNG previews from GEOC folder to consolidated unw_img directory"""
        print(f"\n{'='*80}")
        print(f"Collecting PNG previews from GEOC folders")
        print(f"{'='*80}")
        
        unw_img_dir = os.path.join(self.proj_root, f'unw_img_{self.master_date}')
        os.makedirs(unw_img_dir, exist_ok=True)
        
        png_files = []
        for swath_name in self.swaths_to_process:
            geoc_root = os.path.join(self.proj_root, swath_name, 'GEOC')
            
            if not os.path.exists(geoc_root):
                print(f"  ⚠ GEOC directory not found: {geoc_root}")
                continue
            
            for root, dirs, files in os.walk(geoc_root):
                for file in files:
                    if file.endswith('.png'):
                        png_files.append(os.path.join(root, file))
        
        if not png_files:
            print(f"  ⚠ No PNG files found in GEOC directories")
            return False
        
        copied_count = 0
        skipped_count = 0
        
        for src_png in png_files:
            basename = os.path.basename(src_png)
            dst_png = os.path.join(unw_img_dir, basename)
            
            if os.path.exists(dst_png):
                skipped_count += 1
                continue
            
            try:
                shutil.copy2(src_png, dst_png)
                copied_count += 1
            except Exception as e:
                print(f"  ✗ Failed to copy {basename}: {e}")
        
        print(f"  ✓ Copied {copied_count} PNG files to {unw_img_dir}")
        if skipped_count > 0:
            print(f"  ⚠ Skipped {skipped_count} existing files")
        
        return True
    
    def generate_footprint_kml(self, input_tif, output_kml):
        """Generate rectangular KML footprint directly from GeoTIFF bounds."""
        try:
            print(f"  Generating KML from bounds: {input_tif}")

            import json
            info_cmd = ['gdalinfo', '-json', input_tif]
            info_result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=120)
            if info_result.returncode != 0:
                raise RuntimeError(info_result.stderr.strip() or 'gdalinfo failed')

            info = json.loads(info_result.stdout)
            corners = info.get('cornerCoordinates', {})
            ll = corners.get('lowerLeft')
            ur = corners.get('upperRight')
            if not ll or not ur:
                raise RuntimeError('Could not read cornerCoordinates from gdalinfo output')

            min_lon = float(ll[0])
            min_lat = float(ll[1])
            max_lon = float(ur[0])
            max_lat = float(ur[1])

            output_dir = Path(output_kml).parent
            output_dir.mkdir(parents=True, exist_ok=True)

            kml_content = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<kml xmlns=\"http://www.opengis.net/kml/2.2\">
    <Document>
        <name>Bounding Box</name>
        <Style id=\"noFillRedBorder\">
            <LineStyle>
                <color>ff0000ff</color>
                <width>2</width>
            </LineStyle>
            <PolyStyle>
                <color>00000000</color>
            </PolyStyle>
        </Style>
        <Placemark>
            <name>Raster Footprint</name>
            <styleUrl>#noFillRedBorder</styleUrl>
            <Polygon>
                <outerBoundaryIs>
                    <LinearRing>
                        <coordinates>
                            {min_lon:.9f},{min_lat:.9f},0
                            {min_lon:.9f},{max_lat:.9f},0
                            {max_lon:.9f},{max_lat:.9f},0
                            {max_lon:.9f},{min_lat:.9f},0
                            {min_lon:.9f},{min_lat:.9f},0
                        </coordinates>
                    </LinearRing>
                </outerBoundaryIs>
            </Polygon>
        </Placemark>
    </Document>
</kml>
"""

            with open(output_kml, 'w', encoding='utf-8') as f:
                f.write(kml_content)

            print(f"  ✓ Created KML: {output_kml}")
            print(f"    Bounds: lon[{min_lon:.9f}, {max_lon:.9f}] lat[{min_lat:.9f}, {max_lat:.9f}]")
            return True

        except Exception as e:
            print(f"  WARNING: KML generation from bounds failed: {e}")

        print("  Falling back to SAFE preview KML extraction...")
        return self._extract_kml_from_safe(output_kml)
    
    def _extract_kml_from_safe(self, output_kml):
        """Extract KML from first input ZIP file's SAFE/preview/*.kml."""
        try:
            import zipfile
            import tempfile

            input_data = self.config.get('input_data', '')
            if not input_data or not os.path.exists(input_data):
                print(f"  ERROR: input_data directory not found: {input_data}")
                return False

            zip_files = sorted(glob.glob(os.path.join(input_data, '*.zip')))
            if not zip_files:
                print(f"  ERROR: No ZIP files found in {input_data}")
                return False

            with zipfile.ZipFile(zip_files[0], 'r') as zf:
                kml_files = [name for name in zf.namelist() if name.endswith('.kml') and 'preview' in name]
                if not kml_files:
                    print("  ERROR: No preview KML found in SAFE")
                    return False

                kml_file = kml_files[0]
                with tempfile.TemporaryDirectory() as tmpdir:
                    zf.extract(kml_file, tmpdir)
                    src_kml = os.path.join(tmpdir, kml_file)
                    shutil.copy(src_kml, output_kml)

            if os.path.exists(output_kml):
                print(f"  ✓ Copied SAFE KML: {output_kml}")
                return True
        except Exception as e:
            print(f"  WARNING: SAFE KML extraction failed: {e}")
            return False

        return False
    
    @staticmethod
    def _mad_dom_combined_bad_mask(arr, prelude_void_mask, mod_z_thresh=3.5,
                                   pct_thresh=1.0, sentinel_low=-1000.0):
        import numpy as np

        arr = np.asarray(arr, dtype=np.float32)
        prelude = np.asarray(prelude_void_mask, dtype=bool)
        finite = np.isfinite(arr.astype(np.float64))
        eligible = finite & (~prelude)

        base_bad = prelude | (~finite)

        subs = np.asarray(arr[eligible], dtype=np.float64).ravel()
        if subs.size == 0:
            return np.ones(arr.shape, dtype=bool)

        median_val = float(np.median(subs))
        mad = float(np.median(np.abs(subs - median_val)))
        eps = 1e-10

        total_px = arr.size

        rnd = np.round(subs.astype(np.float64)).astype(np.int64)
        unique, counts = np.unique(rnd, return_counts=True)
        idx_sorted = np.argsort(-counts.astype(np.int64))

        outlier_vals = []
        for i in idx_sorted:
            vr = unique[i]
            c = counts[i]
            pct = (c / float(total_px)) * 100.0
            vv = float(vr)
            z = 0.6745 * (vv - median_val) / (mad + eps)
            if abs(z) > float(mod_z_thresh) and pct > float(pct_thresh):
                outlier_vals.append(vv)

        mask_out = np.zeros(arr.shape, dtype=bool)

        sentinel_f = np.float32(sentinel_low)
        for vv in outlier_vals:
            if vv < sentinel_low:
                mask_out |= arr <= sentinel_f
            else:
                mask_out |= np.abs(arr - np.float32(vv)) < np.float32(1.0)

        return np.logical_or(base_bad, mask_out)

    def _postprocess_snap_elevation_dem_to_float32(
            self, input_tif, output_tif, snap_dem_sentinel=-32768):
        import numpy as np
        try:
            from osgeo import gdal
            gdal.UseExceptions()

            ds = gdal.Open(os.path.abspath(input_tif), gdal.GA_ReadOnly)
            if not ds:
                return False
            band = ds.GetRasterBand(1)
            arr_raw = band.ReadAsArray()
            nv = band.GetNoDataValue()
            arr = np.asarray(arr_raw, dtype=np.float32)

            void = (~np.isfinite(arr)) | (arr <= np.float32(-30000))
            void |= np.isfinite(arr) & (
                np.abs(arr.astype(np.float64) - float(snap_dem_sentinel)) < 1.0)
            if nv is not None and np.isfinite(nv):
                void |= np.isfinite(arr) & (arr == np.float32(nv))

            nx, ny = ds.RasterXSize, ds.RasterYSize

            combined_bad = self._mad_dom_combined_bad_mask(
                arr, void, mod_z_thresh=3.5, pct_thresh=1.0, sentinel_low=-1000.0)
            nz = int(np.sum(combined_bad))

            valid = ~combined_bad
            out = np.zeros((ny, nx), dtype=np.float32)
            if np.any(valid):
                out[valid] = np.abs(arr[valid])

            nz_pct = nz / float(arr.size) * 100.0 if arr.size else 0.0
            print(
                f"    DEM post-process (MAD+dominant->0): void/outlier/Nan->0 ({nz} px "
                f"{nz_pct:.2f}%), valid elevations -> abs (>=0), NoData=0"
            )

            drv = gdal.GetDriverByName('GTiff')
            if drv is None:
                return False

            stamp = output_tif + '.wrt_tmp.tif'
            out_opts = ['COMPRESS=DEFLATE', 'INTERLEAVE=BAND']
            od = drv.Create(stamp, nx, ny, 1, gdal.GDT_Float32, out_opts)
            if od is None:
                return False

            od.SetGeoTransform(ds.GetGeoTransform())
            wkt = ds.GetProjection()
            if wkt:
                od.SetProjection(wkt)
            od.SetMetadataItem('AREA_OR_POINT', 'PIXEL')
            obr = od.GetRasterBand(1)
            obr.SetNoDataValue(np.float64(0.0))
            obr.WriteArray(out)
            obr.SetNoDataValue(np.float64(0.0))
            obr.FlushCache()
            od.FlushCache()
            od = None
            ds = None

            subprocess.run(
                ['gdal_edit.py', '-a_srs', 'EPSG:4326', '-mo', 'AREA_OR_POINT=PIXEL',
                 '-a_nodata', '0', stamp],
                capture_output=True, text=True)

            if os.path.exists(output_tif):
                try:
                    os.remove(output_tif)
                except OSError:
                    pass
            shutil.move(stamp, output_tif)
            return True

        except Exception as exc:
            print(f"    WARNING: SNAP DEM post-process (numpy/GDAL) failed: {exc}")
            wt = output_tif + '.wrt_tmp.tif'
            if os.path.exists(wt):
                try:
                    os.remove(wt)
                except OSError:
                    pass
            return False

    def _dem_hgt_geotiff_nodata0_finalize(self, tif_path):
        import numpy as np
        stamp = tif_path + '.nodata0_finalize.tif'
        try:
            from osgeo import gdal
            gdal.UseExceptions()
            ds = gdal.Open(os.path.abspath(tif_path), gdal.GA_ReadOnly)
            if not ds:
                return False
            band = ds.GetRasterBand(1)
            arr = np.asarray(band.ReadAsArray(), dtype=np.float32)
            arr[~np.isfinite(arr)] = np.float32(0.0)

            drv = gdal.GetDriverByName('GTiff')
            nx, ny = ds.RasterXSize, ds.RasterYSize
            od = drv.Create(stamp, nx, ny, 1, gdal.GDT_Float32,
                            ['COMPRESS=DEFLATE', 'INTERLEAVE=BAND'])
            if od is None:
                ds = None
                return False
            od.SetGeoTransform(ds.GetGeoTransform())
            wkt = ds.GetProjection()
            if wkt:
                od.SetProjection(wkt)
            od.SetMetadataItem('AREA_OR_POINT', 'PIXEL')
            obr = od.GetRasterBand(1)
            obr.WriteArray(arr)
            obr.SetNoDataValue(np.float64(0.0))
            obr.FlushCache()
            od.FlushCache()
            od = None
            ds = None
            subprocess.run(
                ['gdal_edit.py', '-a_srs', 'EPSG:4326', '-mo', 'AREA_OR_POINT=PIXEL',
                 '-a_nodata', '0', stamp],
                capture_output=True, text=True)
            os.remove(tif_path)
            shutil.move(stamp, tif_path)
            return True
        except Exception as e:
            print(f"    WARNING: DEM NoData=0 finalize failed: {e}")
            if os.path.exists(stamp):
                try:
                    os.remove(stamp)
                except OSError:
                    pass
            return False

    def _geotiff_mad_clean_nodata0(self, tif_path, log_label='GeoTIFF'):
        import numpy as np
        stamp = tif_path + '.madclean_tmp.tif'
        try:
            from osgeo import gdal
            gdal.UseExceptions()
            ds = gdal.Open(os.path.abspath(tif_path), gdal.GA_ReadOnly)
            if not ds:
                return False
            band = ds.GetRasterBand(1)
            arr = np.asarray(band.ReadAsArray(), dtype=np.float32)

            prelude = np.zeros(arr.shape, dtype=bool)
            combined_bad = self._mad_dom_combined_bad_mask(
                arr, prelude, mod_z_thresh=3.5, pct_thresh=1.0, sentinel_low=-1000.0)
            nz = int(np.sum(combined_bad))

            out = np.zeros_like(arr, dtype=np.float32)
            valid = ~combined_bad
            if np.any(valid):
                out[valid] = arr[valid]
            nz_pct = nz / float(arr.size) * 100.0 if arr.size else 0.0
            print(
                f"    [{log_label}] MAD+dominant->0 {os.path.basename(tif_path)}: "
                f"{nz} px ({nz_pct:.2f}%); NaN/outliers->0 only (sign preserved)"
            )

            nx, ny = ds.RasterXSize, ds.RasterYSize
            drv = gdal.GetDriverByName('GTiff')
            od = drv.Create(stamp, nx, ny, 1, gdal.GDT_Float32,
                            ['COMPRESS=DEFLATE', 'INTERLEAVE=BAND'])
            if od is None:
                ds = None
                return False
            od.SetGeoTransform(ds.GetGeoTransform())
            wkt = ds.GetProjection()
            if wkt:
                od.SetProjection(wkt)
            od.SetMetadataItem('AREA_OR_POINT', 'PIXEL')
            obr = od.GetRasterBand(1)
            obr.WriteArray(out)
            obr.SetNoDataValue(np.float64(0.0))
            obr.FlushCache()
            od.FlushCache()
            od = None
            ds = None
            os.remove(tif_path)
            shutil.move(stamp, tif_path)
            subprocess.run(
                ['gdal_edit.py', '-a_srs', 'EPSG:4326',
                 '-mo', 'AREA_OR_POINT=PIXEL', '-a_nodata', '0', tif_path],
                capture_output=True, text=True)
            return True
        except Exception as e:
            print(f"    WARNING: MAD clean failed [{log_label}] {os.path.basename(tif_path)}: {e}")
            if os.path.exists(stamp):
                try:
                    os.remove(stamp)
                except OSError:
                    pass
            return False

    def gdal_postprocess_geotiff(
            self, input_tif, output_tif, is_coherence=False, dem_snap_int16=False):
        temp_tif = output_tif + '.tmp.tif'
        try:
            if is_coherence:
                cmd = [
                    'gdal_translate',
                    '-ot', 'Byte',
                    '-scale', '0', '1', '1', '254',
                    '-a_nodata', '0',
                    '-a_srs', 'EPSG:4326',
                    '-mo', 'AREA_OR_POINT=PIXEL',
                    '-co', 'COMPRESS=DEFLATE',
                    '-co', 'INTERLEAVE=BAND',
                    input_tif, temp_tif
                ]
            elif dem_snap_int16:
                if self._postprocess_snap_elevation_dem_to_float32(input_tif, temp_tif):
                    shutil.move(temp_tif, output_tif)
                    return True
                print(
                    "    WARNING: SNAP DEM post-process failed; copying raw export")
                shutil.copy(input_tif, output_tif)
                return False
            else:
                cmd = [
                    'gdal_translate',
                    '-ot', 'Float32',
                    '-a_nodata', 'nan',
                    '-a_srs', 'EPSG:4326',
                    '-mo', 'AREA_OR_POINT=PIXEL',
                    '-co', 'COMPRESS=DEFLATE',
                    '-co', 'INTERLEAVE=BAND',
                    input_tif, temp_tif
                ]

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"    WARNING: gdal_translate failed: {result.stderr}")
                shutil.copy(input_tif, output_tif)
                return False
            
            shutil.move(temp_tif, output_tif)
            return True

        except Exception as exc:
            print(f"    WARNING: Post-processing failed: {exc}")
            if os.path.exists(temp_tif):
                os.remove(temp_tif)
            return False

    def reset_geotiff_stats(self, tif_path):
        """Clear and recompute GDAL statistics for a GeoTIFF."""
        try:
            subprocess.run(['gdal_edit.py', '-unsetstats', tif_path], capture_output=True, text=True)
            subprocess.run(['gdalinfo', '-stats', tif_path], capture_output=True, text=True)
        except Exception as e:
            print(f"    WARNING: Failed to reset stats for {tif_path}: {e}")

    def gmt_filter_geotiff(self, input_tif, band_type):
        try:
            cmd_info = ['gmt', 'grdinfo', input_tif, '-La']
            result = subprocess.run(cmd_info, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"    WARNING: gmt grdinfo failed: {result.stderr}")
                return None

            mode = None
            lmsscale = None
            for line in result.stdout.split('\n'):
                if 'mode:' in line.lower():
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if 'mode:' in part.lower():
                            mode = float(parts[i + 1])
                        if 'lmsscale:' in part.lower():
                            lmsscale = float(parts[i + 1])

            if mode is None or lmsscale is None:
                print("    WARNING: Could not parse mode/lmsscale from gmt grdinfo")
                return None

            upper_limit = mode + lmsscale
            lower_limit = mode - lmsscale

            base_name = os.path.basename(input_tif).replace('.tif', '')
            output_dir = os.path.dirname(input_tif)
            nc_temp = os.path.join(output_dir, f"{base_name}_data.nc")

            cmd_clip = [
                'gmt', 'grdclip', input_tif,
                f"-G{nc_temp}",
                f"-Sa{upper_limit}/NaN",
                f"-Sb{lower_limit}/NaN"
            ]

            result = subprocess.run(cmd_clip, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"    WARNING: gmt grdclip failed: {result.stderr}")
                return None

            if band_type == 'elevation':
                nc_temp2 = os.path.join(output_dir, f"{base_name}_filt_data.nc")
                cmd_clip2 = [
                    'gmt', 'grdclip', nc_temp,
                    f"-G{nc_temp2}",
                    "-Sb0/NaN+e"
                ]
                result = subprocess.run(cmd_clip2, capture_output=True, text=True)
                if result.returncode != 0:
                    print(f"    WARNING: gmt grdclip (elevation <0) failed: {result.stderr}")
                    return None
                if os.path.exists(nc_temp):
                    os.remove(nc_temp)
                nc_temp = nc_temp2

            output_tif = os.path.join(output_dir, f"filt_{base_name}.tif")
            cmd_convert = ['gmt', 'grdconvert', nc_temp, f"-G{output_tif}=gd:GTiff"]
            result = subprocess.run(cmd_convert, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"    WARNING: gmt grdconvert failed: {result.stderr}")
                return None

            if os.path.exists(nc_temp):
                os.remove(nc_temp)

            print(f"    ✓ Filtered GeoTIFF created: {output_tif}")
            return output_tif

        except Exception as e:
            print(f"    WARNING: GMT filtering failed: {e}")
            return None

    def normalize_intensity_to_byte(self, input_tif, output_tif):
        temp_nodata = None
        try:
            temp_nodata = input_tif + '.nodata.tif'
            cmd_nodata = ['gdal_translate', '-a_nodata', '0.0',
                          '-of', 'GTiff', input_tif, temp_nodata]
            result = subprocess.run(cmd_nodata, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"    WARNING: gdal_translate failed: {result.stderr}")
                shutil.copy(input_tif, output_tif)
                return False

            cmd_edit = ['gdal_edit.py', '-mo', 'AREA_OR_POINT=PIXEL', temp_nodata]
            subprocess.run(cmd_edit, capture_output=True, text=True)

            cmd_proj = ['gdal_edit.py', '-a_srs', 'EPSG:4326', temp_nodata]
            subprocess.run(cmd_proj, capture_output=True, text=True)

            shutil.move(temp_nodata, output_tif)
            print(f"    ✓ Exported intensity as raw float32 amplitude: {os.path.basename(output_tif)}")
            return True

        except Exception as e:
            print(f"    WARNING: Intensity export failed: {e}")
            if temp_nodata and os.path.exists(temp_nodata):
                os.remove(temp_nodata)
            return False
    
    @staticmethod
    def _collect_geoc_tiff_paths(geoc_dir):
        """Paths to all GeoTIFFs under GEOC (pair subfolders + root *.geo.*.tif)."""
        paths = []
        for pair_dir in glob.glob(os.path.join(geoc_dir, '[0-9]*_[0-9]*')):
            paths.extend(glob.glob(os.path.join(pair_dir, '*.tif')))
        paths.extend(glob.glob(os.path.join(geoc_dir, '*.geo.*.tif')))
        return paths

    @staticmethod
    def _geotiff_world_extent_box(ds):
        """Axis-aligned bounding box in dataset CRS: (west, east, south, north)."""
        gt = ds.GetGeoTransform()
        if gt is None or not gt:
            return None
        w = ds.RasterXSize
        h = ds.RasterYSize
        xs, ys = [], []
        for px, py in ((0, 0), (w, 0), (w, h), (0, h)):
            x = gt[0] + px * gt[1] + py * gt[2]
            y = gt[3] + px * gt[4] + py * gt[5]
            xs.append(x)
            ys.append(y)
        return (min(xs), max(xs), min(ys), max(ys))
    
    @staticmethod
    def _intersect_extent_boxes(boxes, eps=1e-9):
        """Intersection of axis-aligned boxes (west, east, south, north)."""
        if not boxes:
            return None
        west = max(b[0] for b in boxes)
        east = min(b[1] for b in boxes)
        south = max(b[2] for b in boxes)
        north = min(b[3] for b in boxes)
        if west >= east - eps or south >= north - eps:
            return None
        return (west, east, south, north)
    
    @staticmethod
    def _spatial_ref_wkt_same(wkt_a, wkt_b):
        """True if two WKT CRS describe the same coordinate system."""
        if not wkt_a or not wkt_b:
            return wkt_a == wkt_b
        try:
            from osgeo import osr
            sa = osr.SpatialReference()
            sb = osr.SpatialReference()
            sa.ImportFromWkt(wkt_a)
            sb.ImportFromWkt(wkt_b)
            return bool(sa.IsSame(sb))
        except Exception:
            return wkt_a.strip() == wkt_b.strip()
    
    def _normalize_geoc_dimensions_fallback_min_pixel(self, geoc_dir, all_tifs):
        """Fallback: min raster width × min height, crop from pixel (0,0)."""
        from osgeo import gdal
        
        min_width = float('inf')
        min_height = float('inf')
        dim_map = {}
        unreadable_tifs = []
        
        for tif_file in all_tifs:
            ds = gdal.Open(tif_file)
            if ds:
                width = ds.RasterXSize
                height = ds.RasterYSize
                dim_map[tif_file] = (width, height)
                min_width = min(min_width, width)
                min_height = min(min_height, height)
                ds = None
            else:
                unreadable_tifs.append(tif_file)
        
        if unreadable_tifs:
            print(f"  ⚠ Could not read {len(unreadable_tifs)} GeoTIFF files with GDAL")
            for tif_file in unreadable_tifs[:5]:
                print(f"    - {tif_file}")
            if len(unreadable_tifs) > 5:
                print(f"    ... and {len(unreadable_tifs)-5} more")
        
        if not dim_map:
            print("  ⚠ No readable GeoTIFF files found; skipping dimension normalization")
            return
        
        min_width = int(min_width)
        min_height = int(min_height)
        print(f"  Fallback target (pixel minima): {min_width}x{min_height}")
        
        needs_crop = any(w != min_width or h != min_height for w, h in dim_map.values())
        if not needs_crop:
            print("  ✓ All files already have identical dimensions")
            return
        
        print("  Cropping with top-left srcwin (fallback)...")
        crop_count = 0
        for tif_file, (width, height) in dim_map.items():
            if width == min_width and height == min_height:
                continue
            temp_file = tif_file + '.crop_temp.tif'
            cmd = [
                'gdal_translate', '-q', '-of', 'GTiff',
                '-srcwin', '0', '0', str(min_width), str(min_height),
                tif_file, temp_file
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0 and os.path.exists(temp_file):
                shutil.move(temp_file, tif_file)
                crop_count += 1
            else:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
        print(f"  ✓ Cropped {crop_count} file(s) (fallback) to {min_width}x{min_height}")
    
    def normalize_geoc_dimensions(self, geoc_dir):
        try:
            from osgeo import gdal
            from osgeo import osr
            
            all_tifs = self._collect_geoc_tiff_paths(geoc_dir)
            if not all_tifs:
                print("  No GeoTIFF files found")
                return
            
            print(f"  Scanning {len(all_tifs)} GeoTIFF files for footprint intersection...")
            
            entries = []
            unreadable = []
            for path in all_tifs:
                ds = gdal.Open(path)
                if not ds:
                    unreadable.append(path)
                    continue
                ext = self._geotiff_world_extent_box(ds)
                srs = None
                try:
                    srs = ds.GetSpatialRef()
                except AttributeError:
                    srs = None
                if srs is None and ds.GetProjection():
                    psrs = osr.SpatialReference()
                    psrs.ImportFromWkt(ds.GetProjection())
                    srs = psrs
                wkt = srs.ExportToWkt() if srs else None
                if ext is None or not wkt:
                    ds = None
                    print(
                        "  ⚠ Missing georeferencing or CRS on at least one file — "
                        "using fallback pixel min width × min height."
                    )
                    self._normalize_geoc_dimensions_fallback_min_pixel(geoc_dir, all_tifs)
                    return
                entries.append({'path': path, 'extent': ext, 'wkt': wkt})
                ds = None
            
            if unreadable:
                print(f"  ⚠ Could not open {len(unreadable)} path(s) with GDAL")
                for p in unreadable[:5]:
                    print(f"    - {p}")
                if len(unreadable) > 5:
                    print(f"    ... and {len(unreadable) - 5} more")
            
            if not entries:
                print("  ⚠ No readable georeferenced GeoTIFFs; skipping dimension normalization")
                return
            
            ref_wkt = entries[0]['wkt']
            for ent in entries[1:]:
                if not self._spatial_ref_wkt_same(ref_wkt, ent['wkt']):
                    print(
                        "  ⚠ CRS mismatch between GeoTIFFs — using fallback pixel min width × min height."
                    )
                    self._normalize_geoc_dimensions_fallback_min_pixel(geoc_dir, all_tifs)
                    return
            
            box = self._intersect_extent_boxes([e['extent'] for e in entries])
            if not box:
                print(
                    "  ⚠ Footprint bounding boxes do not overlap — "
                    "using fallback pixel min width × min height."
                )
                self._normalize_geoc_dimensions_fallback_min_pixel(geoc_dir, all_tifs)
                return
            
            west, east, south, north = box
            ulx, uly, lrx, lry = west, north, east, south
            print(
                f"  Geographic intersection (dataset CRS): "
                f"west={west}, east={east}, south={south}, north={north}"
            )
            print(f"  gdal_translate -projwin: {ulx} {uly} {lrx} {lry}")
            
            gdal_translate = shutil.which('gdal_translate') or 'gdal_translate'
            crop_ok = 0
            for ent in entries:
                path = ent['path']
                temp_file = path + '.crop_tmp.tif'
                cmd = [
                    gdal_translate, '-q', '-of', 'GTiff',
                    '-projwin', str(ulx), str(uly), str(lrx), str(lry),
                    path, temp_file,
                ]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0 and os.path.exists(temp_file):
                    shutil.move(temp_file, path)
                    crop_ok += 1
                else:
                    if os.path.exists(temp_file):
                        try:
                            os.remove(temp_file)
                        except OSError:
                            pass
                    err = (result.stderr or result.stdout or "").strip()
                    print(f"    ⚠ gdal_translate failed for {os.path.basename(path)}: {err[:300]}")
            
            if crop_ok:
                print(f"  ✓ Cropped {crop_ok} file(s) to common geographic intersection")
            ds0 = gdal.Open(entries[0]['path'])
            if ds0:
                print(
                    f"  Output reference size (first file): "
                    f"{ds0.RasterXSize}x{ds0.RasterYSize} px"
                )
                ds0 = None
            
        except Exception as e:
            print(f"  WARNING: Dimension normalization failed: {e}")

    def cleanup_geoc_directory(self, geoc_dir):
        """Remove intermediate files from GEOC directory."""
        try:
            cleanup_count = 0

            for xml_file in glob.glob(os.path.join(geoc_dir, '**/*.xml'), recursive=True):
                os.remove(xml_file)
                cleanup_count += 1

            lia_files = [
                os.path.join(geoc_dir, 'localIncidenceAngle.geo.tif'),
                os.path.join(geoc_dir, 'filt_localIncidenceAngle.geo.tif')
            ]
            for lia_file in lia_files:
                if os.path.exists(lia_file):
                    os.remove(lia_file)
                    cleanup_count += 1

            for nc_file in glob.glob(os.path.join(geoc_dir, '*.nc')):
                os.remove(nc_file)
                cleanup_count += 1

            print(f"  ✓ Removed {cleanup_count} intermediate files")
        except Exception as e:
            print(f"  WARNING: Cleanup failed: {e}")
    
    def step21_export_geotiff(self):
        if self.insar_target == 2:
            print(f"\n{'='*80}")
            print(f"STEP 24: Export GeoTIFF - SKIPPED")
            print(f"{'='*80}")
            print(f"  StaMPS modes (insar_target=2) skip Steps 18-24 and go directly to Steps 25–27 (StaMPS Export)\n")
            return True
        
        print(f"\n{'='*80}")
        print(f"STEP 24: Export GeoTIFF")
        print(f"{'='*80}")
        
        try:
            from osgeo import gdal
            print("  ✓ GDAL Python bindings (osgeo) are available")
        except ImportError as e:
            print(f"\n{'='*80}")
            print(f"ERROR: GDAL Python bindings not installed")
            print(f"{'='*80}")
            print(f"  Step 24 requires the 'osgeo' module (GDAL Python bindings)")
            print(f"  This is needed for GeoTIFF dimension normalization")
            print(f"\n  Install with:")
            print(f"    conda install -c conda-forge gdal")
            print(f"  or")
            print(f"    pip install gdal")
            print(f"\n  Error details: {e}")
            print(f"{'='*80}\n")
            return False
        
        bad_pairs = self.get_bad_pairs()
        
        failed_pairs = self.get_snaphu_failed_pairs()
        
        excluded_pairs = bad_pairs | failed_pairs
        
        if excluded_pairs:
            print(f"\n⚠️  Excluding {len(excluded_pairs)} pairs from export:")
            if bad_pairs:
                print(f"    - {len(bad_pairs)} bad pairs (bad_pair.txt)")
            if failed_pairs:
                print(f"    - {len(failed_pairs)} failed SNAPHU pairs (snaphu_error.log)")
        
        swath_config = self.get_swath_config_value()
        use_merged = swath_config not in [1, 2, 3]
        merge_folder_name = self.get_merge_folder_name(swath_config) if use_merged else None
        
        for swath_name in self.swaths_to_process:
            print(f"\nProcessing swath: {swath_name}")
            
            if use_merged:
                multi_tc_dir = os.path.join(self.proj_root, merge_folder_name, f'multi_tc_{self.master_date}')
                geoc_dir = os.path.join(self.proj_root, merge_folder_name, 'GEOC')
            else:
                multi_tc_dir = os.path.join(self.proj_root, swath_name, f'multi_tc_{self.master_date}')
                geoc_dir = os.path.join(self.proj_root, swath_name, 'GEOC')
            
            if not os.path.exists(multi_tc_dir):
                print(f"  ⚠ Multi-tc directory not found: {multi_tc_dir}")
                continue
            
            pair_folders = []
            for item in os.listdir(multi_tc_dir):
                item_path = os.path.join(multi_tc_dir, item)
                if os.path.isdir(item_path):
                    pair_folders.append(item)
            
            pair_folders = sorted(pair_folders)
            
            if not pair_folders:
                print(f"  No pair folders found in multi_tc")
                continue
            
            first_pair_name = None
            first_tc_dim = None
            
            for pair_folder in pair_folders:
                if pair_folder in excluded_pairs:
                    if pair_folder in bad_pairs:
                        print(f"  Skipping {pair_folder} (bad pair - see bad_pair.txt)")
                    else:
                        print(f"  Skipping {pair_folder} (SNAPHU unwrap failed - see snaphu_error.log)")
                    continue
                
                if first_pair_name is None:
                    first_pair_name = pair_folder
                    first_tc_dim_files = glob.glob(os.path.join(multi_tc_dir, pair_folder, '*.dim'))
                    if first_tc_dim_files:
                        first_tc_dim = sorted(first_tc_dim_files)[0]
                
                print(f"\n  Processing: {pair_folder}")
                
                output_pair_dir = os.path.join(geoc_dir, pair_folder)
                unw_output_check = os.path.join(output_pair_dir, f"{pair_folder}.geo.unw.tif")
                coh_output_check = os.path.join(output_pair_dir, f"{pair_folder}.geo.cc.tif")
                unw_png_check = os.path.join(output_pair_dir, f"{pair_folder}.geo.unw.png")
                
                if os.path.exists(unw_output_check) and os.path.exists(coh_output_check) and os.path.exists(unw_png_check):
                    print(f"    ✓ Outputs already exist, skipping")
                    continue
                
                tc_pair_dir = os.path.join(multi_tc_dir, pair_folder)
                
                dim_files = glob.glob(os.path.join(tc_pair_dir, '*.dim'))
                if not dim_files:
                    print(f"    ⚠ No .dim file found")
                    continue
                
                tc_dim = sorted(dim_files)[0]
                data_dir = tc_dim.replace('.dim', '.data')
                
                if not os.path.isdir(data_dir):
                    print(f"    ⚠ Data directory not found")
                    continue
                
                unwphase_band = None
                coh_band = None
                try:
                    import xml.etree.ElementTree as ET
                    tree = ET.parse(tc_dim)
                    root = tree.getroot()
                    for band in root.findall('.//BAND_NAME'):
                        band_name = band.text
                        if band_name.startswith('UnwPhase_') and not unwphase_band:
                            unwphase_band = band_name
                        elif band_name.startswith('coh_') and not coh_band:
                            coh_band = band_name
                except Exception as e:
                    print(f"    ⚠ Failed to parse bands: {e}")
                    continue
                
                if not unwphase_band:
                    print(f"    ⚠ No UnwPhase band found")
                    continue
                
                print(f"    UnwPhase: {unwphase_band}")
                if coh_band:
                    print(f"    Coherence: {coh_band}")
                
                output_pair_dir = os.path.join(geoc_dir, pair_folder)
                os.makedirs(output_pair_dir, exist_ok=True)
                
                unw_output = os.path.join(output_pair_dir, f"{pair_folder}.geo.unw.tif")
                if not os.path.exists(unw_output):
                    unw_temp = os.path.join(output_pair_dir, f"{pair_folder}.geo.unw.tmp.tif")
                    cmd_unw = [
                        *self.gpt_base_cmd(),
                        'Subset',
                        f"-Ssource={tc_dim}",
                        f"-PsourceBands={unwphase_band}",
                        '-t', unw_temp,
                        '-f', 'GeoTIFF'
                    ]
                    success = self.run_command(cmd_unw, f"Export UnwPhase {pair_folder}")
                    if success and os.path.exists(unw_temp):
                        self.gdal_postprocess_geotiff(unw_temp, unw_output, is_coherence=False)
                        if os.path.exists(unw_temp):
                            os.remove(unw_temp)
                        print(f"    ✓ Exported: {pair_folder}.geo.unw.tif")
                
                if os.path.exists(unw_output):
                    expected_png = os.path.splitext(unw_output)[0] + '.png'
                    if not os.path.exists(expected_png):
                        png_path = self.generate_png_from_geotiff(unw_output)
                        if png_path:
                            print(f"    ✓ Generated PNG: {os.path.basename(png_path)}")
                
                if coh_band:
                    coh_output = os.path.join(output_pair_dir, f"{pair_folder}.geo.cc.tif")
                    if not os.path.exists(coh_output):
                        coh_temp = os.path.join(output_pair_dir, f"{pair_folder}.geo.cc.tmp.tif")
                        cmd_coh = [
                            *self.gpt_base_cmd(),
                            'Subset',
                            f"-Ssource={tc_dim}",
                            f"-PsourceBands={coh_band}",
                            '-t', coh_temp,
                            '-f', 'GeoTIFF'
                        ]
                        success = self.run_command(cmd_coh, f"Export Coherence {pair_folder}")
                        if success and os.path.exists(coh_temp):
                            self.gdal_postprocess_geotiff(coh_temp, coh_output, is_coherence=True)
                            if os.path.exists(coh_temp):
                                os.remove(coh_temp)
                            print(f"    ✓ Exported: {pair_folder}.geo.cc.tif")
            
            if first_pair_name and first_tc_dim:
                print(f"\n  Exporting DEM/ENU/MLI from first pair: {first_pair_name}")
                
                intensity_band = None
                try:
                    import xml.etree.ElementTree as ET
                    tree = ET.parse(first_tc_dim)
                    root = tree.getroot()
                    for band in root.findall('.//BAND_NAME'):
                        band_name = band.text
                        if band_name.startswith('Intensity_'):
                            intensity_band = band_name
                            break
                except Exception as e:
                    print(f"    ⚠ Failed to parse intensity band: {e}")
                
                if intensity_band:
                    mli_output = os.path.join(geoc_dir, f"{first_pair_name}.geo.mli.tif")
                    if not os.path.exists(mli_output):
                        mli_temp = os.path.join(geoc_dir, f"{first_pair_name}.geo.mli.tmp.tif")
                        cmd_mli = [
                            *self.gpt_base_cmd(),
                            'Subset',
                            f"-Ssource={first_tc_dim}",
                            f"-PsourceBands={intensity_band}",
                            '-t', mli_temp,
                            '-f', 'GeoTIFF'
                        ]
                        success = self.run_command(cmd_mli, f"Export Intensity (MLI) {first_pair_name}")
                        if success and os.path.exists(mli_temp):
                            self.normalize_intensity_to_byte(mli_temp, mli_output)
                            if os.path.exists(mli_temp):
                                os.remove(mli_temp)
                else:
                    print(f"    ⚠ No Intensity band found in first pair (skipping mli.geo.tif export)")
                
                hgt_output = os.path.join(geoc_dir, f"{first_pair_name}.geo.hgt.tif")
                if not os.path.exists(hgt_output):
                    hgt_temp = os.path.join(geoc_dir, f"{first_pair_name}.geo.hgt.tmp.tif")
                    cmd_hgt = [
                        *self.gpt_base_cmd(),
                        'Subset',
                        f"-Ssource={first_tc_dim}",
                        "-PsourceBands=elevation",
                        '-t', hgt_temp,
                        '-f', 'GeoTIFF'
                    ]
                    success = self.run_command(cmd_hgt, "Export Elevation")
                    if success and os.path.exists(hgt_temp):
                        self.gdal_postprocess_geotiff(
                            hgt_temp, hgt_output, is_coherence=False, dem_snap_int16=True)
                        if os.path.exists(hgt_temp):
                            os.remove(hgt_temp)
                        print(f"    ✓ Exported: {first_pair_name}.geo.hgt.tif")

                    if os.path.exists(hgt_output):
                        hgt_filtered = self.gmt_filter_geotiff(hgt_output, 'elevation')
                        if hgt_filtered and hgt_filtered != hgt_output:
                            shutil.move(hgt_filtered, hgt_output)
                        self._dem_hgt_geotiff_nodata0_finalize(hgt_output)
                        self.reset_geotiff_stats(hgt_output)
                
                lia_output = os.path.join(geoc_dir, 'localIncidenceAngle.geo.tif')
                if not os.path.exists(lia_output):
                    lia_temp = os.path.join(geoc_dir, 'localIncidenceAngle.geo.tmp.tif')
                    cmd_lia = [
                        *self.gpt_base_cmd(),
                        'Subset',
                        f"-Ssource={first_tc_dim}",
                        "-PsourceBands=localIncidenceAngle",
                        '-t', lia_temp,
                        '-f', 'GeoTIFF'
                    ]
                    success = self.run_command(cmd_lia, "Export LocalIncidenceAngle")
                    if success and os.path.exists(lia_temp):
                        self.gdal_postprocess_geotiff(lia_temp, lia_output, is_coherence=False)
                        if os.path.exists(lia_temp):
                            os.remove(lia_temp)
                        print(f"    ✓ Exported: localIncidenceAngle.geo.tif")

                if os.path.exists(lia_output):
                    lia_filtered = self.gmt_filter_geotiff(lia_output, 'localIncidenceAngle')
                    if lia_filtered:
                        lia_output = lia_filtered
                    self._geotiff_mad_clean_nodata0(lia_output, 'LocalIncidenceAngle')

                orbit = self.config.get('orbit', 'Ascending').strip()
                alpha = -12 if orbit.lower() == 'ascending' else 192
                
                e_output = os.path.join(geoc_dir, f"{first_pair_name}.geo.E.tif")
                n_output = os.path.join(geoc_dir, f"{first_pair_name}.geo.N.tif")
                u_output = os.path.join(geoc_dir, f"{first_pair_name}.geo.U.tif")
                
                if os.path.exists(lia_output):
                    if not os.path.exists(e_output):
                        e_nc = os.path.join(geoc_dir, f"{first_pair_name}_E.nc")
                        cmd_e = [
                            'gmt', 'grdmath', lia_output, 'D2R', 'SIN',
                            str(alpha + 90), 'D2R', 'SIN', 'MUL',
                            '=', e_nc
                        ]
                        subprocess.run(cmd_e, capture_output=True, text=True)
                        subprocess.run(['gmt', 'grdconvert', e_nc, f"-G{e_output}=gd:GTiff"], capture_output=True, text=True)
                        if os.path.exists(e_nc):
                            os.remove(e_nc)
                        if os.path.exists(e_output):
                            print(f"    ✓ Computed: {first_pair_name}.geo.E.tif")

                    if not os.path.exists(n_output):
                        n_nc = os.path.join(geoc_dir, f"{first_pair_name}_N.nc")
                        cmd_n = [
                            'gmt', 'grdmath', lia_output, 'D2R', 'SIN',
                            str(alpha + 90), 'D2R', 'COS', 'MUL', 'NEG',
                            '=', n_nc
                        ]
                        subprocess.run(cmd_n, capture_output=True, text=True)
                        subprocess.run(['gmt', 'grdconvert', n_nc, f"-G{n_output}=gd:GTiff"], capture_output=True, text=True)
                        if os.path.exists(n_nc):
                            os.remove(n_nc)
                        if os.path.exists(n_output):
                            print(f"    ✓ Computed: {first_pair_name}.geo.N.tif")

                    if not os.path.exists(u_output):
                        u_nc = os.path.join(geoc_dir, f"{first_pair_name}_U.nc")
                        cmd_u = [
                            'gmt', 'grdmath', lia_output, 'D2R', 'COS',
                            '=', u_nc
                        ]
                        subprocess.run(cmd_u, capture_output=True, text=True)
                        subprocess.run(['gmt', 'grdconvert', u_nc, f"-G{u_output}=gd:GTiff"], capture_output=True, text=True)
                        if os.path.exists(u_nc):
                            os.remove(u_nc)
                        if os.path.exists(u_output):
                            print(f"    ✓ Computed: {first_pair_name}.geo.U.tif")

            if first_pair_name:
                kml_output = os.path.join(geoc_dir, f"{first_pair_name}-poly.kml")
                if not os.path.exists(kml_output):
                    source_file = None

                    for suffix in ['E', 'N', 'U']:
                        candidate = os.path.join(geoc_dir, f"{first_pair_name}.geo.{suffix}.tif")
                        if os.path.exists(candidate):
                            source_file = candidate
                            break

                    if not source_file:
                        lia_candidate = os.path.join(geoc_dir, 'localIncidenceAngle.geo.tif')
                        if os.path.exists(lia_candidate):
                            source_file = lia_candidate

                    if not source_file:
                        cc_candidate = os.path.join(geoc_dir, first_pair_name, f"{first_pair_name}.geo.cc.tif")
                        if os.path.exists(cc_candidate):
                            source_file = cc_candidate

                    if source_file:
                        self.generate_footprint_kml(source_file, kml_output)
            
            self.normalize_geoc_dimensions(geoc_dir)
            
            self.cleanup_geoc_directory(geoc_dir)
            
            self.copy_metadata_info_to_geoc(geoc_dir)
            
            print(f"\n  ✓ GeoTIFF export complete for {swath_name}")
            
            if use_merged:
                break
        
        if self.insar_target == 1:
            self.copy_geoc_png_previews()
        
        return True
    
    
    def step09_bandmath_export(self):
        if self.insar_target != 2:
            print(f"\n{'='*80}")
            print(f"STEP 12: Bandmath Export - SKIPPED")
            print(f"{'='*80}")
            print(f"  insar_target={self.insar_target} (Bandmath only needed for StaMPS, insar_target=2)\n")
            return True
        
        print(f"\n{'='*80}")
        print(f"STEP 12: Bandmath Export (Method={'SBAS' if self.insar_method == 1 else 'PS'})")
        print(f"{'='*80}")
        
        if not self.sbas_pairs:
            if not self.load_sbas_pairs():
                print(f"  ✗ Failed to load SBAS pairs")
                return False
        
        swath_config = self.get_swath_config_value()
        is_multi_swath = swath_config not in [1, 2, 3]
        aoi_region = self.config.get('clip_roi', '')
        has_subset = bool(aoi_region)

        merge_addband_graph, merge_addband_extra, graph_label = (
            self._merge_addband_graph_for_swath_layout(is_multi_swath))
        if not os.path.exists(merge_addband_graph):
            print(f"  ✗ Graph not found: {merge_addband_graph}")
            return False
        print(f"  Bandmath graph: {graph_label}")
        if is_multi_swath:
            print(f"  DEM model (-Pdem_name_model): {self.get_backgeo_dem_model()}")
        
        if is_multi_swath:
            merge_folder = self.get_merge_folder_name(swath_config)
            merge_dir = os.path.join(self.proj_root, merge_folder)
            
            masked_band = self._stamps_sbas_bandmath_masked_intf_input(True)
            if masked_band is not None:
                intf_input_dir, intf_input_suffix = masked_band
            elif has_subset:
                intf_input_dir = os.path.join(merge_dir, f'multi_intf_deb_mrg_subset_{self.master_date}')
                intf_input_suffix = "_Stack_esd_mask_deb_mmifg_mrg_subset.dim"
            else:
                intf_input_dir = os.path.join(merge_dir, f'multi_intf_deb_mrg_{self.master_date}')
                intf_input_suffix = "_Stack_esd_mask_deb_mmifg_mrg.dim"
            
            bandmath_dir = os.path.join(merge_dir, f'multi_intf_deb_mrg_bandmath_sbas_{self.master_date}')
            
            if not os.path.exists(intf_input_dir):
                print(f"  ✗ Interferogram directory not found: {intf_input_dir}")
                print(f"  Run previous steps to generate required interferogram folder")
                return False
            
            os.makedirs(bandmath_dir, exist_ok=True)
            
            pairs = self.list_pair_dirs_check_only(intf_input_dir)
            if not pairs:
                print(f"  ✗ No pairs found in {intf_input_dir}")
                return False
            
            print(f"  Processing {len(pairs)} pairs")
            print(f"  Input: {os.path.basename(intf_input_dir)}/")
            print(f"  Output: {os.path.basename(bandmath_dir)}/")
            
            for pair_name in pairs:
                if self.insar_target == 2 and self.insar_method == 2 and self._skip_pair_folder_for_current_mode(pair_name):
                    print(f"  Skipping {pair_name}: StaMPS PS excludes SBAS folders involving master")
                    continue
                source_dim = os.path.join(intf_input_dir, pair_name, f"{pair_name}{intf_input_suffix}")
                output_dir = os.path.join(bandmath_dir, pair_name)
                output_dim = os.path.join(output_dir, f"{pair_name}_bandmath.dim")
                
                if os.path.exists(output_dim):
                    print(f"  Skipping {pair_name} (output exists)")
                    continue
                
                if not os.path.exists(source_dim):
                    print(f"  ⚠ Skipping {pair_name}: source not found")
                    continue
                
                os.makedirs(output_dir, exist_ok=True)
                cmd = [
                    *self.gpt_base_cmd(),
                    merge_addband_graph,
                    f"-Pmerged_input={source_dim}",
                    *merge_addband_extra,
                    f"-Poutput_bandmath_int_dir={output_dim}"
                ]
                
                success = self.run_command(cmd, f"Bandmath {pair_name}")
                if not success:
                    print(f"  ✗ Bandmath failed for {pair_name}")
                    continue
        
        else:
            swath_name = self.swaths_to_process[0]
            swath_root = os.path.join(self.proj_root, swath_name)
            
            masked_band = self._stamps_sbas_bandmath_masked_intf_input(False)
            if masked_band is not None:
                intf_input_dir, intf_input_suffix = masked_band
            elif has_subset:
                intf_input_dir = os.path.join(swath_root, f'multi_intf_deb_subset_{self.master_date}')
                intf_input_suffix = "_Stack_esd_mask_deb_mmifg_subset.dim"
            else:
                intf_input_dir = os.path.join(swath_root, f'multi_intf_deb_{self.master_date}')
                intf_input_suffix = "_Stack_esd_mask_deb_mmifg.dim"
            
            bandmath_dir = os.path.join(swath_root, f'multi_intf_deb_bandmath_sbas_{self.master_date}')
            
            if not os.path.exists(intf_input_dir):
                print(f"  ✗ Interferogram directory not found: {intf_input_dir}")
                return False
            
            os.makedirs(bandmath_dir, exist_ok=True)
            
            pairs = self.list_pair_dirs_check_only(intf_input_dir)
            if not pairs:
                print(f"  ✗ No pairs found in {intf_input_dir}")
                return False
            
            print(f"  Processing {len(pairs)} pairs for {swath_name}")
            print(f"  Input: {os.path.basename(intf_input_dir)}/")
            print(f"  Output: {os.path.basename(bandmath_dir)}/")
            
            for pair_name in pairs:
                if self.insar_target == 2 and self.insar_method == 2 and self._skip_pair_folder_for_current_mode(pair_name):
                    print(f"  Skipping {pair_name}: StaMPS PS excludes SBAS folders involving master")
                    continue
                source_dim = os.path.join(intf_input_dir, pair_name, f"{pair_name}{intf_input_suffix}")
                output_dir = os.path.join(bandmath_dir, pair_name)
                output_dim = os.path.join(output_dir, f"{pair_name}_bandmath.dim")
                
                if os.path.exists(output_dim):
                    print(f"  Skipping {pair_name} (output exists)")
                    continue
                
                if not os.path.exists(source_dim):
                    print(f"  ⚠ Skipping {pair_name}: source not found")
                    continue
                
                os.makedirs(output_dir, exist_ok=True)
                cmd = [
                    *self.gpt_base_cmd(),
                    merge_addband_graph,
                    f"-Pmerged_input={source_dim}",
                    *merge_addband_extra,
                    f"-Poutput_bandmath_int_dir={output_dim}"
                ]
                
                success = self.run_command(cmd, f"Bandmath {pair_name}")
                if not success:
                    print(f"  ✗ Bandmath failed for {pair_name}")
                    continue
        
        return True
    
    
