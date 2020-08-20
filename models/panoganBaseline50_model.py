import torch
from .base_model import BaseModel
from . import networks

class panoganBaseline50Model(BaseModel):
    """
        Xseq + Adversarial Feedback Loop
    """
    @staticmethod
    def modify_commandline_options(parser, is_train=True):
        # changing the default values to match the pix2pix paper (https://phillipi.github.io/pix2pix/)
        parser.set_defaults(norm='instance', netG='panoGAN_baseline_G', dataset_mode='aligned4')
        if is_train:
            parser.set_defaults(pool_size=0, gan_mode='vanilla')
            parser.add_argument('--lambda_L1', type=float, default=100.0, help='weight for L1 loss')

        return parser

    def __init__(self, opt):
        BaseModel.__init__(self, opt)
        # specify the training losses you want to print out. The training/test scripts will call <BaseModel.get_current_losses>
        self.loss_names = ['G_GAN', 'G_L1', 'D1_real', 'D1_fake']
        # specify the images you want to save/display. The training/test scripts will call <BaseModel.get_current_visuals>
        if self.isTrain:
            self.visual_names = ['img_A', 'img_B', 'fake_B']
        else:  # during test time, only load G
            self.visual_names = ['fake_B']
        # specify the models you want to save to the disk. The training/test scripts will call <BaseModel.save_networks> and <BaseModel.load_networks>
        self.model_names = ['G', 'D_img']
        # define networks (both generator and discriminator)
        self.netG = networks.define_G(opt.input_nc, opt.output_nc, opt.ngf, opt.netG, opt.norm,
                                      not opt.no_dropout, opt.init_type, opt.init_gain, self.gpu_ids)
        self.netD_img = networks.define_D(2 * opt.input_nc, opt.ndf, opt.netD,
                                       opt.n_layers_D, opt.norm, opt.init_type, opt.init_gain, self.gpu_ids)

        if self.isTrain:
            # define loss functions
            self.criterionGAN = networks.GANLoss(opt.gan_mode).to(self.device)
            self.criterionL1 = torch.nn.L1Loss()
            # initialize optimizers; schedulers will be automatically created by function <BaseModel.setup>.
            self.optimizer_G = torch.optim.Adam(self.netG.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999))
            self.optimizer_D_img = torch.optim.Adam(self.netD_img.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999))
            self.optimizers.append(self.optimizer_G)
            self.optimizers.append(self.optimizer_D_img)

    def set_input(self, input):
        AtoB = self.opt.direction == 'AtoB'
        self.img_A = input['A' if AtoB else 'B'].to(self.device)
        self.img_B = input['B' if AtoB else 'A'].to(self.device)
        self.img_D = input['D'].to(self.device)
        self.image_paths = input['A_paths' if AtoB else 'B_paths']
        self.real_label = 0.9
        self.false_label = 0.0

    def forward(self):
        """Run forward pass; called by both functions <optimize_parameters> and <test>."""
        self.fake_B, _ = self.netG(self.img_A)

    def backward_D(self):
        """Calculate GAN loss for the discriminator"""
        # Fake; stop backprop to the generator by detaching fake_B
        fake_AB = torch.cat((self.img_A, self.fake_B),
                           1)  # we use conditional GANs; we need to feed both input and output to the discriminator
        pred_fake_AB = self.netD_img(fake_AB.detach())  # Fake; stop backprop to the generator by detaching fake_B
        self.loss_D1_fake = self.criterionGAN(pred_fake_AB, self.false_label)
        # Real
        real_AB = torch.cat((self.img_A, self.img_B), 1)
        pred_real_AB = self.netD_img(real_AB)
        self.loss_D1_real = self.criterionGAN(pred_real_AB, self.real_label)

        # combine loss and calculate gradients
        self.loss_D = (self.loss_D1_fake + self.loss_D1_real) * 0.5
        self.loss_D.backward()


    def backward_G(self):
        """Calculate GAN and L1 loss for the generator"""
        # GAN loss
        fake_AB = torch.cat((self.img_A, self.fake_B), 1)  # we use conditional GANs; we need to feed both input and output to the discriminator
        pred_fake_AB = self.netD_img(fake_AB)
        self.loss_G_GAN = self.criterionGAN(pred_fake_AB, self.real_label)
        # L1 loss
        self.loss_G_L1 = self.criterionL1(self.fake_B, self.img_B) * self.opt.lambda_L1

        self.loss_G = self.loss_G_GAN + self.loss_G_L1
        self.loss_G.backward()

    def optimize_parameters(self):
        self.forward()  # compute fake images: G(A)
        # update D
        self.set_requires_grad(self.netD_img, True)  # enable backprop for D
        self.optimizer_D_img.zero_grad()  # set D's gradients to zero
        self.backward_D()  # calculate gradients for D
        self.optimizer_D_img.step()  # update D's weights

        # update G
        self.set_requires_grad(self.netD_img, False)  # D requires no gradients when optimizing G
        self.optimizer_G.zero_grad()  # set G's gradients to zero
        self.backward_G()  # calculate graidents for G
        self.optimizer_G.step()  # udpate G's weights