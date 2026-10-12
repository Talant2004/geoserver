import sys
import numpy as np
import cv2
import rasterio
from rasterio.enums import Resampling
from rasterio.features import sieve, shapes, rasterize
from rasterio.transform import Affine
import geopandas as gpd
import fiona
from shapely.geometry import Polygon, MultiPolygon

# Включаем поддержку KML
fiona.drvsupport.supported_drivers['KML'] = 'rw'
fiona.drvsupport.supported_drivers['LIBKML'] = 'rw'

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QFileDialog, 
                             QDoubleSpinBox, QSpinBox, QSlider, QFormLayout, 
                             QGroupBox, QMessageBox, QStyleOption, QStyle, QSplitter, QCheckBox)
from PyQt6.QtCore import Qt, pyqtSignal, QRect
from PyQt6.QtGui import QImage, QPainter, QColor

class InteractivePreview(QWidget):
    mouse_dragged = pyqtSignal(int, int, int)

    def __init__(self):
        super().__init__()
        self.setStyleSheet("background-color: #222; border: 1px solid #555;")
        self.setMouseTracking(False)
        self.image_rect = None 
        self._raw_qimage = None
        self.preview_shape = None

    def update_image(self, qimage, preview_shape):
        self.preview_shape = preview_shape
        self._raw_qimage = qimage
        self.update() 

    def paintEvent(self, event):
        opt = QStyleOption()
        opt.initFrom(self)
        p = QPainter(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, p, self)
        
        if self._raw_qimage is None:
            p.setPen(QColor(200, 200, 200))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Превью появится здесь")
            return
        
        canvas_rect = self.rect()
        img_size = self._raw_qimage.size()
        scaled_size = img_size.scaled(canvas_rect.size(), Qt.AspectRatioMode.KeepAspectRatio)
        
        x = (canvas_rect.width() - scaled_size.width()) // 2
        y = (canvas_rect.height() - scaled_size.height()) // 2
        self.image_rect = QRect(x, y, scaled_size.width(), scaled_size.height())
        
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(self.image_rect, self._raw_qimage)

    def mousePressEvent(self, event):
        self._handle_mouse(event)

    def mouseMoveEvent(self, event):
        self._handle_mouse(event)

    def _handle_mouse(self, event):
        if not self._raw_qimage or not self.image_rect:
            return
        
        pos = event.pos()
        if self.image_rect.contains(pos):
            img_x = pos.x() - self.image_rect.x()
            img_y = pos.y() - self.image_rect.y()
            
            scale_x = self.preview_shape[1] / self.image_rect.width()
            scale_y = self.preview_shape[0] / self.image_rect.height()
            
            real_x = int(img_x * scale_x)
            real_y = int(img_y * scale_y)

            if event.buttons() & Qt.MouseButton.LeftButton:
                self.mouse_dragged.emit(real_x, real_y, 1) 
            elif event.buttons() & Qt.MouseButton.RightButton:
                self.mouse_dragged.emit(real_x, real_y, 2) 


class DodderDetectorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Интерактивный детектор повилики (HSV & Сравнение)")
        self.setGeometry(50, 50, 1400, 800)
        
        self.rgb_path = None
        self.ndvi_path = None
        
        self.preview_rgb = None
        self.preview_ndvi = None
        self.base_mask = None   
        self.user_mask = None   
        self.valid_mask = None 
        self.preview_kml_mask = None 
        
        self.orig_transform = None
        self.orig_width = None
        self.orig_height = None
        self.orig_crs = None
        self.final_gdf = None 
        
        self.current_preview_bytes = None 
        self.orig_bytes = None
        
        self.initUI()
        
    def initUI(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        
        settings_widget = QWidget()
        settings_widget.setFixedWidth(350)
        settings_layout = QVBoxLayout(settings_widget)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        
        load_group = QGroupBox("1. Загрузка данных")
        load_layout = QVBoxLayout()
        self.btn_load_rgb = QPushButton("Загрузить RGB Ортофотоплан")
        self.btn_load_rgb.clicked.connect(self.load_rgb)
        self.lbl_rgb_status = QLabel("RGB: Не загружен")
        self.btn_load_ndvi = QPushButton("Загрузить NDVI растр (опционально)")
        self.btn_load_ndvi.clicked.connect(self.load_ndvi)
        self.lbl_ndvi_status = QLabel("NDVI: Не загружен")
        load_layout.addWidget(self.btn_load_rgb)
        load_layout.addWidget(self.lbl_rgb_status)
        load_layout.addWidget(self.btn_load_ndvi)
        load_layout.addWidget(self.lbl_ndvi_status)
        load_group.setLayout(load_layout)
        settings_layout.addWidget(load_group)
        
        rt_group = QGroupBox("2. Интерактивный поиск")
        rt_layout = QFormLayout()
        
        self.slider_thresh = QSlider(Qt.Orientation.Horizontal)
        self.slider_thresh.setRange(0, 100)
        self.slider_thresh.setValue(40)
        self.slider_thresh.valueChanged.connect(self.calculate_base_mask)
        
        self.slider_brush = QSlider(Qt.Orientation.Horizontal)
        self.slider_brush.setRange(1, 100)
        self.slider_brush.setValue(20)
        
        self.btn_clear_brush = QPushButton("Сбросить ручные правки")
        self.btn_clear_brush.clicked.connect(self.clear_user_mask)
        
        rt_layout.addRow("Чувствительность HSV:", self.slider_thresh)
        rt_layout.addRow("Размер кисти:", self.slider_brush)
        rt_layout.addRow("", QLabel("<i>ЛКМ: Добавить очаг | ПКМ: Стереть очаг</i>"))
        rt_layout.addRow("", self.btn_clear_brush)
        rt_group.setLayout(rt_layout)
        settings_layout.addWidget(rt_group)
        
        export_group = QGroupBox("3. Расчет и Экспорт KML")
        export_layout = QFormLayout()
        
        self.spin_resolution = QDoubleSpinBox()
        self.spin_resolution.setValue(0.5)
        self.spin_resolution.setSingleStep(0.1)
        
        self.spin_sieve = QSpinBox()
        self.spin_sieve.setRange(1, 10000)
        self.spin_sieve.setValue(50)
        
        self.spin_buffer = QDoubleSpinBox()
        self.spin_buffer.setRange(0, 50)
        self.spin_buffer.setValue(2.0)
        
        self.chk_remove_holes = QCheckBox("Заливка внутренних пустот (убрать дырки)")
        self.chk_remove_holes.setChecked(True)
        
        self.btn_calc = QPushButton("Рассчитать полигоны (KML)")
        self.btn_calc.clicked.connect(self.calculate_polygons)
        self.btn_calc.setEnabled(False)
        
        self.btn_export = QPushButton("Сохранить файл KML")
        self.btn_export.clicked.connect(self.export_kml)
        self.btn_export.setEnabled(False)
        
        export_layout.addRow("Разрешение (м):", self.spin_resolution)
        export_layout.addRow("Мин. размер пятна (пикселей):", self.spin_sieve)
        export_layout.addRow("Буфер (м):", self.spin_buffer)
        export_layout.addRow("", self.chk_remove_holes)
        export_layout.addRow("", self.btn_calc)
        export_layout.addRow("", self.btn_export)
        export_group.setLayout(export_layout)
        settings_layout.addWidget(export_group)
        
        settings_layout.addStretch()
        
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        orig_widget = QWidget()
        orig_layout = QVBoxLayout(orig_widget)
        orig_layout.setContentsMargins(0, 0, 0, 0)
        orig_label = QLabel("Оригинальный снимок")
        orig_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        orig_layout.addWidget(orig_label)
        self.canvas_orig = InteractivePreview()
        orig_layout.addWidget(self.canvas_orig, 1)
        
        mask_widget = QWidget()
        mask_layout = QVBoxLayout(mask_widget)
        mask_layout.setContentsMargins(0, 0, 0, 0)
        mask_label = QLabel("Результат обнаружения")
        mask_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mask_layout.addWidget(mask_label)
        self.canvas_mask = InteractivePreview()
        mask_layout.addWidget(self.canvas_mask, 1)
        
        splitter.addWidget(orig_widget)
        splitter.addWidget(mask_widget)
        
        self.canvas_orig.mouse_dragged.connect(self.apply_brush)
        self.canvas_mask.mouse_dragged.connect(self.apply_brush)
        
        main_layout.addWidget(settings_widget)
        main_layout.addWidget(splitter, 1)

    def load_rgb(self):
        file, _ = QFileDialog.getOpenFileName(self, "RGB растр", "", "TIFF (*.tif *.tiff)")
        if file:
            self.rgb_path = file
            self.lbl_rgb_status.setText(f"RGB: {file.split('/')[-1]}")
            self.btn_calc.setEnabled(True)
            self.prepare_realtime_data()

    def load_ndvi(self):
        file, _ = QFileDialog.getOpenFileName(self, "NDVI растр", "", "TIFF (*.tif *.tiff)")
        if file:
            self.ndvi_path = file
            self.lbl_ndvi_status.setText(f"NDVI: {file.split('/')[-1]}")
            self.prepare_realtime_data()

    def prepare_realtime_data(self):
        if not self.rgb_path: return
        
        target_size = 1000.0 
        
        with rasterio.open(self.rgb_path) as src:
            self.orig_transform = src.transform
            self.orig_width = src.width
            self.orig_height = src.height
            self.orig_crs = src.crs
            
            scale = min(1.0, target_size / max(src.width, src.height))
            out_shape = (int(max(1, src.height * scale)), int(max(1, src.width * scale)))
            img = src.read((1, 2, 3), out_shape=out_shape, resampling=Resampling.bilinear)
            
            nodata_mask = src.read_masks(1, out_shape=out_shape, resampling=Resampling.nearest)
            self.valid_mask = (nodata_mask > 0)
            
            img = np.transpose(img, (1, 2, 0))
            if img.dtype != np.uint8:
                img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
            
            black_bg = (img[:,:,0] == 0) & (img[:,:,1] == 0) & (img[:,:,2] == 0)
            white_bg = (img[:,:,0] == 255) & (img[:,:,1] == 255) & (img[:,:,2] == 255)
            self.valid_mask = self.valid_mask & ~black_bg & ~white_bg
            
            self.preview_rgb = img

        if self.ndvi_path:
            with rasterio.open(self.ndvi_path) as src:
                self.preview_ndvi = src.read(1, out_shape=(out_shape[0], out_shape[1]), resampling=Resampling.bilinear)
                
        h, w, ch = self.preview_rgb.shape
        self.orig_bytes = np.ascontiguousarray(self.preview_rgb).tobytes()
        qimg_orig = QImage(self.orig_bytes, w, h, ch * w, QImage.Format.Format_RGB888)
        self.canvas_orig.update_image(qimg_orig, (h, w))
                
        self.user_mask = np.zeros((out_shape[0], out_shape[1]), dtype=np.uint8)
        self.calculate_base_mask()

    def reset_calculation(self):
        self.preview_kml_mask = None
        self.final_gdf = None
        self.btn_export.setEnabled(False)

    def calculate_base_mask(self):
        if self.preview_rgb is None: return
        self.reset_calculation() 
        
        h, w = self.preview_rgb.shape[:2]
        self.base_mask = np.zeros((h, w), dtype=np.uint8)
        v_slider = self.slider_thresh.value() 
        
        if self.preview_ndvi is not None:
            thresh_val = v_slider / 100.0
            ndvi = self.preview_ndvi
            self.base_mask[(ndvi < thresh_val) & (ndvi > -0.5)] = 1
        else:
            hsv = cv2.cvtColor(self.preview_rgb, cv2.COLOR_RGB2HSV)
            h_min = int(max(5, 25 - v_slider / 5.0))
            h_max = int(min(60, 35 + v_slider / 5.0))
            s_min = int(max(20, 200 - v_slider * 1.8))
            v_min = int(max(20, 200 - v_slider * 1.8))
            
            lower_yellow = np.array([h_min, s_min, v_min])
            upper_yellow = np.array([h_max, 255, 255])
            
            hsv_mask = cv2.inRange(hsv, lower_yellow, upper_yellow)
            self.base_mask[hsv_mask == 255] = 1
            
        if self.valid_mask is not None:
            self.base_mask[~self.valid_mask] = 0
            
        self.update_display()

    def apply_brush(self, x, y, mode):
        if self.user_mask is None: return
        self.reset_calculation() 
        radius = self.slider_brush.value()
        cv2.circle(self.user_mask, (x, y), radius, mode, -1)
        self.update_display()

    def clear_user_mask(self):
        if self.user_mask is not None:
            self.reset_calculation() 
            self.user_mask.fill(0)
            self.update_display()

    def update_display(self):
        if self.preview_rgb is None: return
        
        final_mask = np.where(self.user_mask == 1, 1, np.where(self.user_mask == 2, 0, self.base_mask))
        
        display_img = self.preview_rgb.copy()
        display_img[final_mask == 1] = [255, 0, 0] 
        
        if self.preview_kml_mask is not None:
            contours, _ = cv2.findContours(self.preview_kml_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(display_img, contours, -1, (0, 255, 255), 2) 
        
        h, w, ch = display_img.shape
        display_img = np.ascontiguousarray(display_img)
        
        self.current_preview_bytes = display_img.tobytes()
        qimg_mask = QImage(self.current_preview_bytes, w, h, ch * w, QImage.Format.Format_RGB888)
        
        self.canvas_mask.update_image(qimg_mask, (h, w))

    def fill_holes(self, geom):
        """Утилита для очистки внутренних пустот (сохраняем только внешний контур)"""
        if geom.geom_type == 'Polygon':
            return Polygon(geom.exterior)
        elif geom.geom_type == 'MultiPolygon':
            return MultiPolygon([Polygon(p.exterior) for p in geom.geoms])
        return geom

    def calculate_polygons(self):
        if not self.rgb_path: return

        self.btn_calc.setText("Идет расчет...")
        QApplication.processEvents()
        
        target_res = self.spin_resolution.value()
        v_slider = self.slider_thresh.value()
        
        try:
            with rasterio.open(self.rgb_path) as src:
                pixel_size = abs(src.transform[0])
                target_res_units = target_res / 111320.0 if src.crs and src.crs.is_geographic else target_res
                scale_factor = pixel_size / target_res_units if target_res_units > pixel_size else 1.0
                
                new_width = max(1, int(src.width * scale_factor))
                new_height = max(1, int(src.height * scale_factor))
                
                rgb_data = src.read(out_shape=(src.count, new_height, new_width), resampling=Resampling.bilinear)
                
                nodata_mask = src.read_masks(1, out_shape=(new_height, new_width), resampling=Resampling.nearest)
                valid_mask_hires = (nodata_mask > 0)
                
                rgb_hwc = np.transpose(rgb_data, (1, 2, 0))
                if rgb_hwc.dtype != np.uint8:
                    rgb_hwc = cv2.normalize(rgb_hwc, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)

                black_bg = (rgb_hwc[:,:,0] == 0) & (rgb_hwc[:,:,1] == 0) & (rgb_hwc[:,:,2] == 0)
                white_bg = (rgb_hwc[:,:,0] == 255) & (rgb_hwc[:,:,1] == 255) & (rgb_hwc[:,:,2] == 255)
                valid_mask_hires = valid_mask_hires & ~black_bg & ~white_bg
                
                new_transform = src.transform * src.transform.scale((src.width / new_width), (src.height / new_height))
                crs = src.crs

            final_mask = np.zeros((new_height, new_width), dtype=np.uint8)
            
            if self.ndvi_path:
                with rasterio.open(self.ndvi_path) as ndvi_src:
                    ndvi_data = ndvi_src.read(1, out_shape=(new_height, new_width), resampling=Resampling.bilinear)
                thresh_val = v_slider / 100.0
                final_mask[(ndvi_data < thresh_val) & (ndvi_data > -0.5)] = 1 
            else:
                hsv_data = cv2.cvtColor(rgb_hwc, cv2.COLOR_RGB2HSV)
                h_min = int(max(5, 25 - v_slider / 5.0))
                h_max = int(min(60, 35 + v_slider / 5.0))
                s_min = int(max(20, 200 - v_slider * 1.8))
                v_min = int(max(20, 200 - v_slider * 1.8))
                
                lower_yellow = np.array([h_min, s_min, v_min])
                upper_yellow = np.array([h_max, 255, 255])
                
                mask_hsv = cv2.inRange(hsv_data, lower_yellow, upper_yellow)
                final_mask[mask_hsv == 255] = 1
                
            final_mask[~valid_mask_hires] = 0

            if self.user_mask is not None and np.any(self.user_mask > 0):
                hires_user_mask = cv2.resize(self.user_mask, (new_width, new_height), interpolation=cv2.INTER_NEAREST)
                final_mask = np.where(hires_user_mask == 1, 1, np.where(hires_user_mask == 2, 0, final_mask))

            sieved_mask = sieve(final_mask, size=self.spin_sieve.value(), connectivity=4)
            results = ({'properties': {'val': v}, 'geometry': s} for i, (s, v) in enumerate(shapes(sieved_mask, mask=sieved_mask==1, transform=new_transform)))
            geoms = list(results)
            
            if not geoms:
                QMessageBox.warning(self, "Пусто", "На финальном снимке не найдено очагов.")
                self.btn_calc.setText("Рассчитать полигоны (KML)")
                return

            gdf = gpd.GeoDataFrame.from_features(geoms, crs=crs)

            buffer_m = self.spin_buffer.value()
            if buffer_m > 0:
                is_geographic = gdf.crs.is_geographic
                if is_geographic: gdf = gdf.to_crs("EPSG:3857")
                gdf.geometry = gdf.geometry.buffer(buffer_m)
                gdf = gdf.dissolve()
                if is_geographic: gdf = gdf.to_crs(crs)
            
            # Заливка пустот внутри полигонов
            if self.chk_remove_holes.isChecked():
                gdf.geometry = gdf.geometry.apply(self.fill_holes)
            
            self.final_gdf = gdf
            
            h, w = self.preview_rgb.shape[:2]
            scale_x = self.orig_width / w
            scale_y = self.orig_height / h
            preview_transform = self.orig_transform * Affine.scale(scale_x, scale_y)
            
            self.preview_kml_mask = rasterize(
                [(geom, 1) for geom in self.final_gdf.geometry],
                out_shape=(h, w),
                transform=preview_transform,
                fill=0,
                dtype=np.uint8
            )
            
            self.update_display()
            self.btn_export.setEnabled(True)
            QMessageBox.information(self, "Успех", f"Расчет завершен.\nНайдено очагов: {len(self.final_gdf)}")

        except Exception as e:
            QMessageBox.critical(self, "Ошибка расчета", str(e))
        finally:
            self.btn_calc.setText("Рассчитать полигоны (KML)")

    def export_kml(self):
        if self.final_gdf is None or self.final_gdf.empty: return
        
        file, _ = QFileDialog.getSaveFileName(self, "Сохранить KML", "dodder_zones.kml", "KML (*.kml)")
        if not file: return

        try:
            gdf_export = self.final_gdf.to_crs("EPSG:4326")[['geometry']]
            gdf_export.to_file(file, driver='KML')
            QMessageBox.information(self, "Готово", "Файл успешно сохранен!")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка сохранения", str(e))

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DodderDetectorApp()
    window.show()
    sys.exit(app.exec())
